"""Per-user and per-group LanceDB storage and retrieval using cbrkit.

The public surface:

- :func:`build_search_tool` builds a search tool spanning one or more
  casebases.
- :func:`index_document` upserts the chunks of a single document.
- :func:`unindex_paths` removes the chunks of one or more documents.
- :func:`unindex_subtree` removes every chunk whose document path is at
  or beneath a prefix.
- :func:`sync_index` reconciles a store's index against every metadata
  file on disk; called from the startup consistency check and the
  periodic consistency tick.  cbrkit's :meth:`create_index` already
  diffs against the existing rows so unchanged documents skip
  re-embedding.
- :func:`invalidate_store` drops cached state before a store is wiped.

Workspace mutations call :func:`index_document` / :func:`unindex_paths`
/ :func:`unindex_subtree` synchronously while holding the workspace
lock — there is no background dirty-tracking.
"""

import asyncio
import json
import logging
import shutil
import threading
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cbrkit

from .chunkers.base import DocumentMetadata, RetrievedChunk
from .config import settings
from .converters.base import DOCUMENT_EXTENSION
from .store import Casebase
from .tools.base import SearchPathFilterFunc, apply_prefix
from .tools.retrieval import IndexedStorage, LanceDBSearchTool, SearchResult

__all__ = [
    "build_search_tool",
    "index_document",
    "invalidate_store",
    "sync_index",
    "unindex_paths",
    "unindex_subtree",
]

CHUNK_KEY_SEPARATOR = "::"
STORE_METADATA_FILE = "metadata.json"
LANCEDB_TABLE = "chunks"
METADATA_FILENAME_COLUMN = "filename"

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _ChunkEntry:
    """Loaded chunk metadata for a single indexed chunk."""

    text: str
    token_count: int
    start_line: int
    end_line: int
    start_index: int
    end_index: int
    image_path: str | None = None


@dataclass(slots=True)
class _RetrievalState:
    """Caches LanceDB storages, the embedding function, and chunk metadata.

    The lock is reentrant because :meth:`get_storage` may be called from
    inside another :meth:`_lock`-holding section.
    """

    _storage_cache: dict[str, cbrkit.indexable.lancedb[str]] = field(
        default_factory=dict
    )
    _embedding_func: (
        cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray] | None
    ) = field(default=None)
    _chunk_meta: dict[str, dict[str, _ChunkEntry]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def get_embedding_func(
        self,
    ) -> cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]:
        """Get or create the shared embedding function based on settings."""
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

    def get_storage(self, store: Casebase) -> cbrkit.indexable.lancedb[str]:
        """Return the LanceDB storage for *store*, lazily creating it.

        Validates the embedding fingerprint and loads chunk metadata from
        disk on first access.
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
            self._chunk_meta[key] = _load_all_chunks_from_dir(
                store.metadata_path(settings.data_dir)
            )
            return storage


_state = _RetrievalState()


def _read_store_metadata(lancedb_dir: Path) -> dict[str, Any]:
    """Read the per-store sidecar metadata, returning ``{}`` when absent."""
    try:
        return json.loads(
            (lancedb_dir / STORE_METADATA_FILE).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_store_metadata(lancedb_dir: Path, data: dict[str, Any]) -> None:
    """Persist the per-store sidecar metadata."""
    (lancedb_dir / STORE_METADATA_FILE).write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _validate_fingerprint(store_key: str, lancedb_dir: Path) -> None:
    """Check the embedding fingerprint and wipe stale vector data.

    Must be called while ``_state._lock`` is held.
    """
    metadata = _read_store_metadata(lancedb_dir)
    stored = metadata.get("embedding")
    current = settings.embedding.fingerprint()

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
        metadata = {}

    if stored != current:
        metadata["embedding"] = current
        _write_store_metadata(lancedb_dir, metadata)


def _build_chunk_key(filename: str, chunk_index: int) -> str:
    return f"{filename}{CHUNK_KEY_SEPARATOR}{chunk_index}"


def _parse_chunk_key(key: str) -> tuple[str, int]:
    try:
        filename, index_str = key.rsplit(CHUNK_KEY_SEPARATOR, maxsplit=1)
        return filename, int(index_str)
    except ValueError as exc:
        raise ValueError(f"Invalid chunk key format: {key!r}") from exc


def _escape_sql_literal(value: str) -> str:
    """Escape a string for use inside a single-quoted LanceDB SQL literal."""
    return value.replace("'", "''")


def _load_all_chunks_from_dir(metadata_dir: Path) -> dict[str, _ChunkEntry]:
    """Load all chunks from a metadata directory as a casebase mapping.

    Metadata filenames use the stem-only convention (``report.json`` for
    ``report.md``), so the document extension is re-appended when building
    chunk keys.
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
        chunks.update(_doc_chunk_entries(entry_filename, doc))

    return chunks


def _doc_chunk_entries(
    filename: str, doc: DocumentMetadata
) -> dict[str, _ChunkEntry]:
    """Build the in-memory chunk metadata cache entries for a document."""
    image_path = doc.original_path if doc.entry_kind == "image" else None
    return {
        _build_chunk_key(filename, i): _ChunkEntry(
            text=chunk.text,
            token_count=chunk.token_count,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            start_index=chunk.start_index,
            end_index=chunk.end_index,
            image_path=image_path,
        )
        for i, chunk in enumerate(doc.chunks)
    }


def _delete_by_predicate(
    storage: cbrkit.indexable.lancedb[str], predicate: str
) -> None:
    """Run a raw LanceDB ``DELETE`` against the chunks table.

    Bypasses :meth:`cbrkit.indexable.lancedb.delete_index` so the FTS
    index is not torn down and rebuilt on every per-document mutation —
    LanceDB auto-maintains it across deletes.
    """
    table = storage._table
    if table is None:
        return
    table.delete(predicate)


def _filename_in_predicate(filenames: Collection[str]) -> str:
    """Build a ``filename IN (...)`` SQL predicate."""
    quoted = ", ".join(f"'{_escape_sql_literal(f)}'" for f in filenames)
    return f"{METADATA_FILENAME_COLUMN} IN ({quoted})"


def _filename_subtree_predicate(prefix: str) -> str:
    """Build a predicate matching ``prefix`` exactly or any path under it."""
    escaped = _escape_sql_literal(prefix)
    return (
        f"{METADATA_FILENAME_COLUMN} = '{escaped}' OR "
        f"{METADATA_FILENAME_COLUMN} LIKE '{escaped}/%'"
    )


def _index_document_sync(
    store_key: str,
    storage: cbrkit.indexable.lancedb[str],
    filename: str,
    new_chunks_text: dict[str, str],
    new_chunks_meta: dict[str, _ChunkEntry],
) -> None:
    """Replace the LanceDB rows for *filename* and update the meta cache."""
    key_prefix = f"{filename}{CHUNK_KEY_SEPARATOR}"
    with _state._lock:
        cache = _state._chunk_meta.setdefault(store_key, {})
        old_keys = [k for k in cache if k.startswith(key_prefix)]

        if storage._table is not None:
            _delete_by_predicate(
                storage,
                f"{METADATA_FILENAME_COLUMN} = '{_escape_sql_literal(filename)}'",
            )
        for k in old_keys:
            cache.pop(k, None)

        if new_chunks_text:
            storage.update_index(new_chunks_text)
            cache.update(new_chunks_meta)


def _unindex_paths_sync(
    store_key: str,
    storage: cbrkit.indexable.lancedb[str],
    filenames: Collection[str],
) -> None:
    """Delete LanceDB rows whose filename is in *filenames* and update cache."""
    if not filenames:
        return
    with _state._lock:
        cache = _state._chunk_meta.setdefault(store_key, {})
        target = set(filenames)
        prefixes = tuple(f"{f}{CHUNK_KEY_SEPARATOR}" for f in target)
        keys = [k for k in cache if k.startswith(prefixes)]
        _delete_by_predicate(storage, _filename_in_predicate(target))
        for k in keys:
            cache.pop(k, None)


def _unindex_subtree_sync(
    store_key: str,
    storage: cbrkit.indexable.lancedb[str],
    prefix: str,
) -> None:
    """Delete LanceDB rows under *prefix* and update the meta cache."""
    key_prefixes = (f"{prefix}{CHUNK_KEY_SEPARATOR}", f"{prefix}/")
    with _state._lock:
        cache = _state._chunk_meta.setdefault(store_key, {})
        keys = [k for k in cache if k.startswith(key_prefixes)]
        _delete_by_predicate(storage, _filename_subtree_predicate(prefix))
        for k in keys:
            cache.pop(k, None)


async def index_document(
    store: Casebase, filename: str, doc: DocumentMetadata
) -> None:
    """Replace the index entries for a single document.

    Embeds the new chunks via the shared embedding function and writes
    them to LanceDB, removing any rows tied to the previous version of
    *filename*.  Keeps the in-memory chunk-metadata cache aligned with
    the LanceDB table so queries return up-to-date snippet metadata.
    """
    storage = _state.get_storage(store)
    new_text = {
        _build_chunk_key(filename, i): chunk.text for i, chunk in enumerate(doc.chunks)
    }
    new_meta = _doc_chunk_entries(filename, doc)
    await asyncio.to_thread(
        _index_document_sync, store.store_key, storage, filename, new_text, new_meta
    )


async def unindex_paths(store: Casebase, filenames: Collection[str]) -> None:
    """Remove chunks for the given document paths from the index."""
    if not filenames:
        return
    storage = _state.get_storage(store)
    await asyncio.to_thread(
        _unindex_paths_sync, store.store_key, storage, list(filenames)
    )


async def unindex_subtree(store: Casebase, prefix: str) -> None:
    """Remove every chunk whose path equals *prefix* or starts with ``prefix/``."""
    if not prefix:
        return
    storage = _state.get_storage(store)
    await asyncio.to_thread(_unindex_subtree_sync, store.store_key, storage, prefix)


async def invalidate_store(store: Casebase) -> None:
    """Drop cached state for *store* before its directories are wiped."""
    key = store.store_key
    with _state._lock:
        _state._storage_cache.pop(key, None)
        _state._chunk_meta.pop(key, None)


def sync_index(store: Casebase) -> None:
    """Reconcile the LanceDB index with the metadata files on disk.

    cbrkit's :meth:`create_index` diffs against the existing table at the
    row level and only re-embeds changed/new chunks, so this is cheap
    enough to run on the periodic consistency tick without further
    optimisation.
    """
    key = store.store_key
    storage = _state.get_storage(store)
    loaded = _load_all_chunks_from_dir(store.metadata_path(settings.data_dir))
    casebase = {chunk_key: entry.text for chunk_key, entry in loaded.items()}
    logger.info("Syncing LanceDB index for %s (%d chunks)", key, len(casebase))
    storage.create_index(casebase)
    with _state._lock:
        _state._chunk_meta[key] = loaded


def _to_retrieved_chunk(
    result: SearchResult,
    meta: _ChunkEntry,
) -> RetrievedChunk:
    """Map a raw :class:`SearchResult` plus cached metadata to a :class:`RetrievedChunk`."""
    filename, chunk_index = _parse_chunk_key(result.key)
    return RetrievedChunk(
        filename=filename,
        chunk_index=chunk_index,
        text=result.text,
        token_count=meta.token_count,
        score=round(result.score, 4),
        start_line=meta.start_line,
        end_line=meta.end_line,
        start_index=meta.start_index,
        end_index=meta.end_index,
        image_path=meta.image_path,
    )


def _make_key_filter(
    file_filter: Callable[[str], bool],
) -> Callable[[str], bool]:
    """Build a key filter that extracts the filename and delegates to *file_filter*."""

    def key_filter(key: str) -> bool:
        return file_filter(_parse_chunk_key(key)[0])

    return key_filter


def build_search_tool(
    stores: Sequence[Casebase],
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> LanceDBSearchTool[RetrievedChunk]:
    """Build a :class:`LanceDBSearchTool` spanning one or more casebases.

    The index is assumed up-to-date — every workspace mutation maintains
    it inline.  Per-store filters are wired so each backing storage only
    surfaces results passing the caller's filename predicate, and
    ``@group/`` prefixing is applied to result keys.
    """
    chunk_meta: dict[str, _ChunkEntry] = {}
    indexed: list[IndexedStorage] = []

    with _state._lock:
        for store in stores:
            storage = _state.get_storage(store)
            prefix = store.prefix
            for key, entry in _state._chunk_meta.get(store.store_key, {}).items():
                chunk_meta[apply_prefix(prefix, key)] = entry

            file_filter = filter_for_store(store) if filter_for_store else None
            key_filter: Callable[[str], bool] | None = (
                _make_key_filter(file_filter) if file_filter is not None else None
            )
            indexed.append(
                IndexedStorage(
                    storage=storage,
                    prefix=prefix,
                    filter_func=key_filter,
                )
            )

    def _result_mapper(result: SearchResult) -> RetrievedChunk:
        return _to_retrieved_chunk(result, chunk_meta[result.key])

    return LanceDBSearchTool(
        storages=tuple(indexed),
        result_mapper=_result_mapper,
    )
