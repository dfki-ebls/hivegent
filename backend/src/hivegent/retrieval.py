"""Per-user and per-group LanceDB storage and retrieval using cbrkit."""

import asyncio
import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable, Sequence

import cbrkit

from .chunkers.base import DocumentMetadata, RetrievedChunk
from .config import settings
from .converters.base import DOCUMENT_EXTENSION
from .store import Casebase
from .tools.base import SearchPathFilterFunc, apply_prefix
from .tools.retrieval import IndexedStorage, LanceDBSearchTool, SearchResult

__all__ = [
    "build_search_tool",
    "invalidate_store",
    "mark_dirty",
    "mark_dirty_and_sync",
    "sync_index",
]

CHUNK_KEY_SEPARATOR = "::"

logger = logging.getLogger(__name__)

EMBEDDING_FINGERPRINT_FILE = "embedding_config.json"
LANCEDB_TABLE = "chunks"
METADATA_FILENAME_COLUMN = "filename"


@dataclass(slots=True, frozen=True)
class _ChunkEntry:
    """Loaded chunk metadata for a single indexed chunk."""

    text: str
    token_count: int
    image_path: str | None = None


@dataclass(slots=True)
class _RetrievalState:
    """Thread-safe singleton managing LanceDB storage and embeddings."""

    _storage_cache: dict[str, cbrkit.indexable.lancedb[str]] = field(
        default_factory=dict
    )
    _embedding_func: (
        cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray] | None
    ) = field(default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending_reindex: set[str] = field(default_factory=set)
    _chunk_meta: dict[str, dict[str, _ChunkEntry]] = field(default_factory=dict)

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
        with self._lock:
            self._pending_reindex.add(store_key)

    def is_dirty(self, store_key: str) -> bool:
        """Check whether a store's search index needs a rebuild.

        Args:
            store_key: The store key to check.

        Returns:
            True if the store is pending reindexing.
        """
        with self._lock:
            return store_key in self._pending_reindex

    def clear_dirty(self, store_key: str) -> None:
        """Clear the dirty flag for a store.

        Args:
            store_key: The store key to clear.
        """
        with self._lock:
            self._pending_reindex.discard(store_key)

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

        embedding_func = self.get_embedding_func()

        with self._lock:
            if key in self._storage_cache:
                return self._storage_cache[key]

            lancedb_dir = store.lancedb_dir(settings.data_dir)
            _validate_fingerprint(key, lancedb_dir)

            storage: cbrkit.indexable.lancedb[str] = cbrkit.indexable.lancedb(
                uri=str(lancedb_dir),
                table=LANCEDB_TABLE,
                index_type="hybrid",
                conversion_func=embedding_func,
                metadata_func=lambda key, _: {
                    METADATA_FILENAME_COLUMN: _parse_chunk_key(key)[0]
                },
            )
            self._storage_cache[key] = storage
            return storage


_state = _RetrievalState()


def _validate_fingerprint(store_key: str, lancedb_dir: Path) -> None:
    """Check the embedding fingerprint and wipe stale vector data.

    Must be called while ``_state._lock`` is held.

    Reads the stored fingerprint from the LanceDB directory.
    If it exists and differs from the current config, the directory
    is wiped and the store is scheduled for re-indexing.

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
        _state._storage_cache.pop(store_key, None)
        _state._embedding_func = None
        shutil.rmtree(lancedb_dir)
        lancedb_dir.mkdir(parents=True, exist_ok=True)
        _state._pending_reindex.add(store_key)

    if stored != current:
        fp_path.write_text(json.dumps(current), encoding="utf-8")


def _build_chunk_key(filename: str, chunk_index: int) -> str:
    return f"{filename}{CHUNK_KEY_SEPARATOR}{chunk_index}"


def _parse_chunk_key(key: str) -> tuple[str, int]:
    try:
        filename, index_str = key.rsplit(CHUNK_KEY_SEPARATOR, maxsplit=1)
        return filename, int(index_str)
    except ValueError as exc:
        raise ValueError(f"Invalid chunk key format: {key!r}") from exc


def _load_all_chunks_from_dir(metadata_dir: Path) -> dict[str, _ChunkEntry]:
    """Load all chunks from a metadata directory as a casebase mapping.

    Metadata filenames use the stem-only convention (``report.json`` for
    ``report.md``), so the document extension is re-appended when building
    chunk keys.

    Args:
        metadata_dir: The directory containing metadata JSON files.

    Returns:
        Dict mapping chunk keys to :class:`_ChunkEntry` instances.
    """
    if not metadata_dir.exists():
        return {}

    chunks: dict[str, _ChunkEntry] = {}

    for meta_file in sorted(metadata_dir.rglob("*.json")):
        stem = str(meta_file.relative_to(metadata_dir).as_posix()).removesuffix(".json")
        doc_filename = stem + DOCUMENT_EXTENSION
        try:
            doc = DocumentMetadata.model_validate_json(
                meta_file.read_text(encoding="utf-8")
            )
        except Exception as exc:
            logger.warning("Failed to load metadata for %s: %s", doc_filename, exc)
            continue

        entry_filename = doc.description_path or doc_filename
        image_path = doc.original_path if doc.entry_kind == "image" else None
        for i, chunk in enumerate(doc.chunks):
            key = _build_chunk_key(entry_filename, i)
            chunks[key] = _ChunkEntry(
                text=chunk.text,
                token_count=chunk.token_count,
                image_path=image_path,
            )

    return chunks


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
        _state._chunk_meta.pop(key, None)
        _state._pending_reindex.discard(key)


def mark_dirty(store: Casebase) -> None:
    """Mark a store's search index as needing a rebuild.

    The next call to :func:`build_search_tool` will resolve the pending
    reindex before performing any search.

    Args:
        store: The casebase to mark.
    """
    _state.mark_dirty(store.store_key)


async def _eager_sync(store: Casebase) -> None:
    """Run :func:`sync_index` in a thread, swallowing errors."""
    try:
        await asyncio.to_thread(sync_index, store)
    except Exception:
        logger.warning("Eager sync failed for %s", store.store_key)


def mark_dirty_and_sync(store: Casebase) -> None:
    """Mark a store dirty and eagerly schedule a background index sync.

    Falls back silently when no running event loop is available (e.g. in
    tests or synchronous contexts).

    Args:
        store: The casebase to mark and sync.
    """
    mark_dirty(store)
    try:
        asyncio.get_running_loop().create_task(_eager_sync(store))
    except RuntimeError:
        pass


def sync_index(store: Casebase) -> None:
    """Rebuild the LanceDB index from all chunk JSON files.

    Loads every chunk for the store and calls ``create_index()`` which
    diffs against the existing table and only adds/removes changed rows.

    Args:
        store: The casebase to sync.
    """
    storage = _state.get_storage(store)
    loaded = _load_all_chunks_from_dir(store.metadata_dir(settings.data_dir))
    casebase = {key: entry.text for key, entry in loaded.items()}
    logger.info(
        "Syncing LanceDB index for %s (%d chunks)", store.store_key, len(casebase)
    )
    storage.create_index(casebase)
    with _state._lock:
        _state._chunk_meta[store.store_key] = loaded
    _state.clear_dirty(store.store_key)


def _ensure_chunk_meta(store: Casebase) -> None:
    """Lazily load and cache chunk metadata for a store that is already indexed.

    Args:
        store: The casebase whose chunk metadata should be cached.
    """
    key = store.store_key
    with _state._lock:
        if key in _state._chunk_meta:
            return

    loaded = _load_all_chunks_from_dir(store.metadata_dir(settings.data_dir))
    with _state._lock:
        if key not in _state._chunk_meta:
            _state._chunk_meta[key] = loaded


def _to_retrieved_chunk(
    result: SearchResult,
    meta: _ChunkEntry | None = None,
) -> RetrievedChunk:
    """Map a raw :class:`SearchResult` to a :class:`RetrievedChunk`.

    Args:
        result: The raw search result.
        meta: Cached chunk metadata.  When ``None``, token count falls
            back to ``len(text.split())``.
    """
    filename, chunk_index = _parse_chunk_key(result.key)
    return RetrievedChunk(
        filename=filename,
        chunk_index=chunk_index,
        text=result.text,
        token_count=meta.token_count if meta is not None else len(result.text.split()),
        score=round(result.score, 4),
        image_path=meta.image_path if meta is not None else None,
    )


def build_search_tool(
    stores: Sequence[Casebase],
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> LanceDBSearchTool[RetrievedChunk]:
    """Build a :class:`LanceDBSearchTool` spanning one or more casebases.

    Syncs any pending re-indexes and creates LanceDB storages as needed.
    Results are automatically mapped to :class:`RetrievedChunk` with
    per-store filtering and ``@group/`` prefixing.

    Args:
        stores: Casebases to search across.
        filter_for_store: Optional callable returning a filename filter
            for each store.  ``None`` means no filtering.

    Returns:
        A configured :class:`LanceDBSearchTool` ready to use.
    """
    for store in stores:
        if _state.is_dirty(store.store_key):
            sync_index(store)
        else:
            _ensure_chunk_meta(store)

    # Build per-store chunk metadata with prefixed keys.
    chunk_meta: dict[str, _ChunkEntry] = {}
    indexed: list[IndexedStorage] = []

    with _state._lock:
        for store in stores:
            prefix = store.prefix
            meta = _state._chunk_meta.get(store.store_key)
            if meta is not None:
                for key, entry in meta.items():
                    prefixed_key = apply_prefix(prefix, key)
                    chunk_meta[prefixed_key] = entry

            file_filter = filter_for_store(store) if filter_for_store else None
            key_filter: Callable[[str], bool] | None = (
                (lambda key, ff=file_filter: ff(_parse_chunk_key(key)[0]))  # type: ignore[misc]
                if file_filter is not None
                else None
            )
            indexed.append(
                IndexedStorage(
                    storage=_state.get_storage(store),
                    prefix=prefix,
                    filter_func=key_filter,
                )
            )

    def _result_mapper(result: SearchResult) -> RetrievedChunk:
        return _to_retrieved_chunk(result, chunk_meta.get(result.key))

    return LanceDBSearchTool(
        storages=tuple(indexed),
        result_mapper=_result_mapper,
    )
