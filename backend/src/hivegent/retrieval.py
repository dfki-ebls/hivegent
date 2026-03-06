"""Per-user and per-group LanceDB storage and retrieval using cbrkit."""

import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any

import cbrkit

from .config import settings
from .store import Casebase
from .tools.retrieval import LanceDBSearchTool, SearchType
from .types import DocumentFilter
from .types import DocumentMetadata, RetrievedChunk

__all__ = [
    "apply_search_tool",
    "build_search_tool",
    "build_where_clause",
    "invalidate_store",
    "mark_dirty",
    "sync_index",
]

CHUNK_KEY_SEPARATOR = "::"

logger = logging.getLogger(__name__)

EMBEDDING_FINGERPRINT_FILE = "embedding_config.json"
LANCEDB_TABLE = "chunks"
METADATA_FILENAME_COLUMN = "filename"


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

    def mark_dirty(self, store_key: str) -> None:
        """Mark a store's search index as needing a rebuild.

        Args:
            store_key: The store key to mark.
        """
        self._pending_reindex.add(store_key)

    def is_dirty(self, store_key: str) -> bool:
        """Check whether a store's search index needs a rebuild.

        Args:
            store_key: The store key to check.

        Returns:
            True if the store is pending reindexing.
        """
        return store_key in self._pending_reindex

    def clear_dirty(self, store_key: str) -> None:
        """Clear the dirty flag for a store.

        Args:
            store_key: The store key to clear.
        """
        self._pending_reindex.discard(store_key)

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
            self.mark_dirty(store_key)

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


def _escape_sql(value: str) -> str:
    """Escape single quotes for SQL string literals."""
    return value.replace("'", "''")


def build_where_clause(
    document_filter: DocumentFilter | None,
    filter_column: str = "filename",
) -> str | None:
    """Build a LanceDB SQL WHERE clause from a document filter.

    Args:
        document_filter: Optional include/exclude filter.
        filter_column: Metadata column name used for SQL WHERE clauses.

    Returns:
        A SQL WHERE string, or ``None`` if no filtering is needed.
    """
    if not document_filter:
        return None

    conditions: list[str] = []

    if document_filter.included:
        include_parts: list[str] = []
        for entry in sorted(document_filter.included):
            escaped = _escape_sql(entry)
            if entry.endswith("/"):
                include_parts.append(f"{filter_column} LIKE '{escaped}%'")
            else:
                include_parts.append(f"{filter_column} = '{escaped}'")
        conditions.append(f"({' OR '.join(include_parts)})")

    if document_filter.excluded:
        for entry in sorted(document_filter.excluded):
            escaped = _escape_sql(entry)
            if entry.endswith("/"):
                conditions.append(f"{filter_column} NOT LIKE '{escaped}%'")
            else:
                conditions.append(f"{filter_column} != '{escaped}'")

    if not conditions:
        return None

    return " AND ".join(conditions)


def _build_chunk_key(filename: str, chunk_index: int) -> str:
    return f"{filename}{CHUNK_KEY_SEPARATOR}{chunk_index}"


def _parse_chunk_key(key: str) -> tuple[str, int]:
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
    filename, _ = _parse_chunk_key(key)
    return {METADATA_FILENAME_COLUMN: filename}


def _load_all_chunks_from_dir(metadata_dir: Path) -> dict[str, str]:
    """Load all chunks from a metadata directory as a casebase mapping.

    Metadata filenames use the stem-only convention (``report.json`` for
    ``report.md``), so the document extension is re-appended when building
    chunk keys.

    Args:
        metadata_dir: The directory containing metadata JSON files.

    Returns:
        Dict mapping chunk keys to chunk text.
    """
    from .config import DOCUMENT_EXTENSION

    if not metadata_dir.exists():
        return {}

    casebase: dict[str, str] = {}

    for meta_file in sorted(metadata_dir.rglob("*.json")):
        stem = str(meta_file.relative_to(metadata_dir).as_posix()).removesuffix(".json")
        doc_filename = stem + DOCUMENT_EXTENSION
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            doc = DocumentMetadata.model_validate(data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to load metadata for %s: %s", doc_filename, exc)
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
        _state.clear_dirty(key)


def mark_dirty(store: Casebase) -> None:
    """Mark a store's search index as needing a rebuild.

    The next call to :func:`build_search_tool` will resolve the pending
    reindex before performing any search.

    Args:
        store: The casebase to mark.
    """
    _state.mark_dirty(store.store_key)


def sync_index(store: Casebase) -> None:
    """Rebuild the LanceDB index from all chunk JSON files.

    Loads every chunk for the store and calls ``create_index()`` which
    diffs against the existing table and only adds/removes changed rows.

    Args:
        store: The casebase to sync.
    """
    _state.clear_dirty(store.store_key)
    storage = _state.get_storage(store)
    casebase = _load_all_chunks_from_dir(store.metadata_dir(settings.data_dir))
    logger.info(
        "Syncing LanceDB index for %s (%d chunks)", store.store_key, len(casebase)
    )
    storage.create_index(casebase)


def build_search_tool(
    stores: Sequence[Casebase],
) -> LanceDBSearchTool[str]:
    """Build a :class:`LanceDBSearchTool` spanning one or more casebases.

    Syncs any pending re-indexes and creates LanceDB storages as needed.

    Args:
        stores: Casebases to search across.

    Returns:
        A configured :class:`LanceDBSearchTool` ready to use.
    """
    for store in stores:
        if _state.is_dirty(store.store_key):
            sync_index(store)
    return LanceDBSearchTool(
        storages=[_state.get_storage(s) for s in stores],
    )


def apply_search_tool(
    stores: Sequence[Casebase],
    search_type: SearchType,
    query: str,
    top_k: int,
    filter_for_store: Callable[[Casebase], DocumentFilter | None] = lambda _: None,
) -> list[RetrievedChunk]:
    """Search across one or more casebases and return merged results.

    Args:
        stores: Casebases to search across.
        search_type: ``"dense"``, ``"sparse"``, or ``"hybrid"``.
        query: Natural language search query.
        top_k: Maximum number of results.
        filter_for_store: Callback returning the document filter for a store.

    Returns:
        List of retrieved chunks sorted by score descending.
    """
    tool = build_search_tool(stores)
    where_clauses = [
        build_where_clause(filter_for_store(s), METADATA_FILENAME_COLUMN)
        for s in stores
    ]
    return [
        RetrievedChunk(
            filename=filename,
            chunk_index=chunk_index,
            text=r.text,
            token_count=len(r.text.split()),
            score=round(r.score, 4),
        )
        for r in tool(
            query,
            top_k=top_k,
            search_type=search_type,
            where_clauses=where_clauses,
        )
        for filename, chunk_index in [_parse_chunk_key(r.key)]
    ]
