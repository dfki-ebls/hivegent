"""Per-user LanceDB storage and retrieval using cbrkit."""

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import cbrkit

from .config import settings
from .types import DocumentFilter

__all__ = [
    "parse_chunk_key",
    "search_dense",
    "search_sparse",
    "sync_index",
]

logger = logging.getLogger(__name__)

CHUNK_KEY_SEPARATOR = "::"
LANCEDB_TABLE = "chunks"
METADATA_FILENAME_COLUMN = "filename"


def _escape_sql(value: str) -> str:
    """Escape single quotes for SQL string literals.

    Args:
        value: The raw string value.

    Returns:
        The escaped string safe for embedding in SQL.
    """
    return value.replace("'", "''")


@dataclass(slots=True)
class _RetrievalState:
    """Thread-safe singleton managing LanceDB storage and embeddings."""

    _storage_cache: dict[str, cbrkit.indexable.lancedb[str]] = field(
        default_factory=dict
    )
    _embedding_func: cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray] | None = field(default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get_embedding_func(self) -> cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]:
        """Get or create the shared embedding function based on settings.

        Returns:
            An embedding function.
        """
        if self._embedding_func is not None:
            return self._embedding_func

        with self._lock:
            if self._embedding_func is not None:
                return self._embedding_func

            cfg = settings.embedding
            if cfg.provider == "openai":
                from openai import AsyncOpenAI

                self._embedding_func = cbrkit.sim.embed.openai(
                    model=cfg.model,
                    client=AsyncOpenAI(
                        api_key=cfg.api_key or None,
                        base_url=cfg.base_url or None,
                    ),
                )
            else:
                self._embedding_func = cbrkit.sim.embed.sentence_transformers(
                    model=cfg.model
                )

            return self._embedding_func

    def get_user_storage(self, user_id: str) -> cbrkit.indexable.lancedb[str]:
        """Get or create the LanceDB storage for a user.

        Args:
            user_id: The user ID.

        Returns:
            The LanceDB storage instance.
        """
        if user_id in self._storage_cache:
            return self._storage_cache[user_id]

        with self._lock:
            if user_id in self._storage_cache:
                return self._storage_cache[user_id]

            uri = str(settings.get_user_lancedb_dir(user_id))
            embed_func = self.get_embedding_func()

            storage: cbrkit.indexable.lancedb[str] = cbrkit.indexable.lancedb(
                uri=uri,
                table=LANCEDB_TABLE,
                index_type="hybrid",
                conversion_func=embed_func,
                metadata_func=_metadata_func,
            )
            self._storage_cache[user_id] = storage
            return storage


_state = _RetrievalState()


def _build_chunk_key(filename: str, chunk_index: int) -> str:
    """Build a chunk key from filename and index.

    Args:
        filename: The document filename.
        chunk_index: The chunk index within the document.

    Returns:
        A string key like ``"report.md::3"``.
    """
    return f"{filename}{CHUNK_KEY_SEPARATOR}{chunk_index}"


def parse_chunk_key(key: str) -> tuple[str, int]:
    """Parse a chunk key back to filename and index.

    Args:
        key: A chunk key like ``"report.md::3"``.

    Returns:
        Tuple of ``(filename, chunk_index)``.

    Raises:
        ValueError: If the key format is invalid.
    """
    try:
        filename, index_str = key.rsplit(CHUNK_KEY_SEPARATOR, maxsplit=1)
        return filename, int(index_str)
    except ValueError as exc:
        raise ValueError(f"Invalid chunk key format: {key!r}") from exc


def _metadata_func(key: str, value: str) -> dict[str, Any]:
    """Extract metadata from a chunk key for LanceDB storage.

    Stores the filename so it can be used in WHERE clauses.

    Args:
        key: The chunk key.
        value: The chunk text (not used for metadata extraction).

    Returns:
        Dict with the filename column.
    """
    filename, _ = parse_chunk_key(key)
    return {METADATA_FILENAME_COLUMN: filename}


def _load_all_chunks(user_id: str) -> dict[str, str]:
    """Load all chunks for a user as a casebase mapping.

    Args:
        user_id: The user ID.

    Returns:
        Dict mapping chunk keys to chunk text.
    """
    from .chunks import ChunkedDocument

    chunks_dir = settings.get_user_chunks_dir(user_id)
    if not chunks_dir.exists():
        return {}

    casebase: dict[str, str] = {}

    for chunk_file in sorted(chunks_dir.rglob("*.json")):
        doc_filename = str(
            chunk_file.relative_to(chunks_dir).as_posix()
        ).removesuffix(".json")
        try:
            data = json.loads(chunk_file.read_text(encoding="utf-8"))
            doc = ChunkedDocument.model_validate(data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to load chunks for %s: %s", doc_filename, exc)
            continue

        for chunk in doc.chunks:
            key = _build_chunk_key(doc_filename, chunk.index)
            casebase[key] = chunk.text

    return casebase


def sync_index(user_id: str) -> None:
    """Rebuild the LanceDB index from all chunk JSON files.

    Loads every chunk for the user and calls ``create_index()`` which
    diffs against the existing table and only adds/removes changed rows.

    Args:
        user_id: The user ID.
    """
    storage = _state.get_user_storage(user_id)
    casebase = _load_all_chunks(user_id)
    logger.info(
        "Syncing LanceDB index for user %s (%d chunks)", user_id, len(casebase)
    )
    storage.create_index(casebase)


def _build_where_clause(
    document_filter: DocumentFilter | None,
) -> str | None:
    """Build a LanceDB SQL WHERE clause for document filtering.

    Args:
        document_filter: Optional include/exclude filter.

    Returns:
        A SQL WHERE string, or ``None`` if no filtering is needed.
    """
    conditions: list[str] = []

    if not document_filter:
        return None

    if document_filter.included:
        include_parts: list[str] = []
        for entry in sorted(document_filter.included):
            escaped = _escape_sql(entry)
            if entry.endswith("/"):
                include_parts.append(
                    f"{METADATA_FILENAME_COLUMN} LIKE '{escaped}%'"
                )
            else:
                include_parts.append(
                    f"{METADATA_FILENAME_COLUMN} = '{escaped}'"
                )
        conditions.append(f"({' OR '.join(include_parts)})")

    if document_filter.excluded:
        for entry in sorted(document_filter.excluded):
            escaped = _escape_sql(entry)
            if entry.endswith("/"):
                conditions.append(
                    f"{METADATA_FILENAME_COLUMN} NOT LIKE '{escaped}%'"
                )
            else:
                conditions.append(
                    f"{METADATA_FILENAME_COLUMN} != '{escaped}'"
                )

    if not conditions:
        return None

    return " AND ".join(conditions)


def _search(
    user_id: str,
    query: str,
    search_type: Literal["dense", "sparse"],
    top_k: int,
    document_filter: DocumentFilter | None,
) -> Sequence[tuple[str, str, float]]:
    """Run a search against the user's LanceDB index.

    Args:
        user_id: The user ID.
        query: The search query.
        search_type: ``"dense"`` or ``"sparse"``.
        top_k: Maximum number of results.
        document_filter: Optional document filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples sorted by score.
    """
    storage = _state.get_user_storage(user_id)
    if not storage.has_index():
        return []

    where = _build_where_clause(document_filter)

    retriever = cbrkit.retrieval.dropout(
        cbrkit.retrieval.lancedb(
            storage=storage,
            search_type=search_type,
            where=where,
            normalize_scores=True,
        ),
        limit=top_k,
    )

    result = cbrkit.retrieval.apply_query_indexed(query, retriever)
    step = result.final_step.queries["default"]

    return [
        (key, step.casebase[key], float(step.similarities[key]))
        for key in step.ranking
    ]


def search_dense(
    user_id: str,
    query: str,
    top_k: int = 5,
    document_filter: DocumentFilter | None = None,
) -> Sequence[tuple[str, str, float]]:
    """Dense vector search over a user's chunks.

    Args:
        user_id: The user ID.
        query: Natural language search query.
        top_k: Maximum number of results.
        document_filter: Optional document include/exclude filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples.
    """
    return _search(user_id, query, "dense", top_k, document_filter)


def search_sparse(
    user_id: str,
    query: str,
    top_k: int = 5,
    document_filter: DocumentFilter | None = None,
) -> Sequence[tuple[str, str, float]]:
    """Sparse BM25/FTS search over a user's chunks.

    Args:
        user_id: The user ID.
        query: Natural language search query.
        top_k: Maximum number of results.
        document_filter: Optional document include/exclude filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples.
    """
    return _search(user_id, query, "sparse", top_k, document_filter)
