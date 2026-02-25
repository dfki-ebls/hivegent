"""Per-user and per-group LanceDB storage and retrieval using cbrkit."""

import json
import logging
import shutil
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import cbrkit

from .config import settings
from .store import Casebase
from .types import ChunkedDocument, DocumentFilter, RetrievedChunk

__all__ = [
    "invalidate_store",
    "parse_chunk_key",
    "search_dense",
    "search_multi",
    "search_sparse",
    "sync_index",
]

logger = logging.getLogger(__name__)

CHUNK_KEY_SEPARATOR = "::"
EMBEDDING_FINGERPRINT_FILE = "embedding_config.json"
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
    _embedding_func: (
        cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray] | None
    ) = field(default=None)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _pending_reindex: set[str] = field(default_factory=set)

    def get_embedding_func(
        self,
    ) -> cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]:
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

    def _validate_fingerprint(self, store_key: str, lancedb_dir: Path) -> None:
        """Check the embedding fingerprint and wipe stale vector data.

        Reads the stored fingerprint from the LanceDB directory.
        If it exists and differs from the current config, the directory
        is wiped and the store is scheduled for re-indexing.
        The current fingerprint is always written back afterwards.

        Args:
            store_key: The store key (for logging and reindex tracking).
            lancedb_dir: Path to the store's LanceDB directory.
        """
        fp_path = lancedb_dir / EMBEDDING_FINGERPRINT_FILE
        current = settings.embedding.fingerprint()

        try:
            stored = json.loads(fp_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            stored = None

        if stored is not None and stored != current:
            logger.warning(
                "Embedding config changed for %s "
                "(was %s, now %s) — wiping LanceDB directory",
                store_key,
                stored,
                current,
            )
            self._storage_cache.pop(store_key, None)
            self._embedding_func = None
            shutil.rmtree(lancedb_dir)
            lancedb_dir.mkdir(parents=True, exist_ok=True)
            self._pending_reindex.add(store_key)

        fp_path.write_text(json.dumps(current), encoding="utf-8")

    def get_storage(self, store: Casebase) -> cbrkit.indexable.lancedb[str]:
        """Get or create the LanceDB storage for a casebase.

        Validates the embedding fingerprint on first access and
        invalidates stale vector data when the model config changes.

        Args:
            store: The casebase identifier.

        Returns:
            The LanceDB storage instance.
        """
        key = store.store_key
        if key in self._storage_cache:
            return self._storage_cache[key]

        with self._lock:
            if key in self._storage_cache:
                return self._storage_cache[key]

            lancedb_dir = store.lancedb_dir(settings.data_dir)
            self._validate_fingerprint(key, lancedb_dir)

            storage: cbrkit.indexable.lancedb[str] = cbrkit.indexable.lancedb(
                uri=str(lancedb_dir),
                table=LANCEDB_TABLE,
                index_type="hybrid",
                conversion_func=self.get_embedding_func(),
                metadata_func=_metadata_func,
            )
            self._storage_cache[key] = storage
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


def _load_all_chunks_from_dir(chunks_dir: Path) -> dict[str, str]:
    """Load all chunks from a chunks directory as a casebase mapping.

    Args:
        chunks_dir: The directory containing chunk JSON files.

    Returns:
        Dict mapping chunk keys to chunk text.
    """
    if not chunks_dir.exists():
        return {}

    casebase: dict[str, str] = {}

    for chunk_file in sorted(chunks_dir.rglob("*.json")):
        doc_filename = str(chunk_file.relative_to(chunks_dir).as_posix()).removesuffix(
            ".json"
        )
        try:
            data = json.loads(chunk_file.read_text(encoding="utf-8"))
            doc = ChunkedDocument.model_validate(data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to load chunks for %s: %s", doc_filename, exc)
            continue

        for i, chunk in enumerate(doc.chunks):
            key = _build_chunk_key(doc_filename, i)
            casebase[key] = chunk.text

    return casebase


def invalidate_store(store: Casebase) -> None:
    """Remove a casebase from the retrieval cache.

    Call this before wiping a store's LanceDB directory so that stale
    connections are not reused.

    Args:
        store: The casebase to evict.
    """
    key = store.store_key
    with _state._lock:
        _state._storage_cache.pop(key, None)
        _state._pending_reindex.discard(key)


def sync_index(store: Casebase) -> None:
    """Rebuild the LanceDB index from all chunk JSON files.

    Loads every chunk for the store and calls ``create_index()`` which
    diffs against the existing table and only adds/removes changed rows.

    Args:
        store: The casebase to sync.
    """
    _state._pending_reindex.discard(store.store_key)
    storage = _state.get_storage(store)
    casebase = _load_all_chunks_from_dir(store.chunks_dir(settings.data_dir))
    logger.info(
        "Syncing LanceDB index for %s (%d chunks)", store.store_key, len(casebase)
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
                include_parts.append(f"{METADATA_FILENAME_COLUMN} LIKE '{escaped}%'")
            else:
                include_parts.append(f"{METADATA_FILENAME_COLUMN} = '{escaped}'")
        conditions.append(f"({' OR '.join(include_parts)})")

    if document_filter.excluded:
        for entry in sorted(document_filter.excluded):
            escaped = _escape_sql(entry)
            if entry.endswith("/"):
                conditions.append(f"{METADATA_FILENAME_COLUMN} NOT LIKE '{escaped}%'")
            else:
                conditions.append(f"{METADATA_FILENAME_COLUMN} != '{escaped}'")

    if not conditions:
        return None

    return " AND ".join(conditions)


def _search_storage(
    storage: cbrkit.indexable.lancedb[str],
    store_key: str,
    query: str,
    search_type: Literal["dense", "sparse"],
    top_k: int,
    document_filter: DocumentFilter | None,
) -> Sequence[tuple[str, str, float]]:
    """Run a search against a single LanceDB storage instance.

    Args:
        storage: The LanceDB storage to search.
        store_key: Store key for pending reindex check.
        query: The search query.
        search_type: ``"dense"`` or ``"sparse"``.
        top_k: Maximum number of results.
        document_filter: Optional document filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples sorted by score.
    """
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
        (key, step.casebase[key], float(step.similarities[key])) for key in step.ranking
    ]


def _search(
    store: Casebase,
    query: str,
    search_type: Literal["dense", "sparse"],
    top_k: int,
    document_filter: DocumentFilter | None,
) -> Sequence[tuple[str, str, float]]:
    """Run a search against a casebase's LanceDB index.

    Args:
        store: The casebase to search.
        query: The search query.
        search_type: ``"dense"`` or ``"sparse"``.
        top_k: Maximum number of results.
        document_filter: Optional document filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples sorted by score.
    """
    if store.store_key in _state._pending_reindex:
        sync_index(store)

    storage = _state.get_storage(store)
    return _search_storage(
        storage, store.store_key, query, search_type, top_k, document_filter
    )


def search_dense(
    store: Casebase,
    query: str,
    top_k: int = 5,
    document_filter: DocumentFilter | None = None,
) -> Sequence[tuple[str, str, float]]:
    """Dense vector search over a store's chunks.

    Args:
        store: The casebase to search.
        query: Natural language search query.
        top_k: Maximum number of results.
        document_filter: Optional document include/exclude filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples.
    """
    return _search(store, query, "dense", top_k, document_filter)


def search_sparse(
    store: Casebase,
    query: str,
    top_k: int = 5,
    document_filter: DocumentFilter | None = None,
) -> Sequence[tuple[str, str, float]]:
    """Sparse BM25/FTS search over a store's chunks.

    Args:
        store: The casebase to search.
        query: Natural language search query.
        top_k: Maximum number of results.
        document_filter: Optional document include/exclude filter.

    Returns:
        List of ``(chunk_key, text, score)`` tuples.
    """
    return _search(store, query, "sparse", top_k, document_filter)


def search_multi(
    stores: Sequence[Casebase],
    search_type: Literal["dense", "sparse"],
    query: str,
    top_k: int = 5,
    document_filter: DocumentFilter | None = None,
    group_filters: dict[str, DocumentFilter] | None = None,
) -> list[RetrievedChunk]:
    """Search across multiple casebases and merge results.

    Results are merged by score. Returns at most ``top_k`` results
    sorted by score descending.

    Args:
        stores: Sequence of casebases to search.
        search_type: ``"dense"`` or ``"sparse"``.
        query: Natural language search query.
        top_k: Maximum number of merged results.
        document_filter: Optional document filter for user stores.
        group_filters: Optional per-group document filters, keyed by
            group ID.  Group stores without an entry are unfiltered.

    Returns:
        List of :class:`RetrievedChunk` results.
    """
    all_results: list[tuple[float, RetrievedChunk]] = []

    for store in stores:
        if store.kind == "user":
            store_filter = document_filter
        else:
            store_filter = (group_filters or {}).get(store.id)
        try:
            results = _search(store, query, search_type, top_k, store_filter)
        except Exception:
            logger.warning("Search failed for store %s", store.store_key, exc_info=True)
            continue
        for chunk_key, text, score in results:
            filename, chunk_index = parse_chunk_key(chunk_key)
            all_results.append(
                (
                    score,
                    RetrievedChunk(
                        store_key=store.store_key,
                        filename=filename,
                        chunk_index=chunk_index,
                        text=text,
                        token_count=len(text.split()),
                        score=round(score, 4),
                    ),
                )
            )

    all_results.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in all_results[:top_k]]
