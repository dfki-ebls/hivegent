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
  periodic consistency tick.  cbrkit's :meth:`put_index` already
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
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cbrkit

from .chunkers.base import DocumentMetadata, RetrievedChunk
from .config import settings
from .converters.base import DOCUMENT_EXTENSION
from .llm import create_openai_client
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
    """Per-chunk metadata used to enrich search results."""

    token_count: int
    start_line: int
    end_line: int
    start_index: int
    end_index: int
    image_path: str | None = None


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


def _build_chunk_key(filename: str, chunk_index: int) -> str:
    return f"{filename}{CHUNK_KEY_SEPARATOR}{chunk_index}"


def _parse_chunk_key(key: str) -> tuple[str, int]:
    try:
        filename, index_str = key.rsplit(CHUNK_KEY_SEPARATOR, maxsplit=1)
        return filename, int(index_str)
    except ValueError as exc:
        raise ValueError(f"Invalid chunk key format: {key!r}") from exc


def _sql_str(value: str) -> str:
    """Quote a string for inlining as a LanceDB SQL literal."""
    return "'" + value.replace("'", "''") + "'"


_SQL_LIKE_ESCAPE = "\\"


def _sql_like_pattern(prefix: str) -> str:
    """Build a LIKE pattern matching paths beneath *prefix*, escaping wildcards.

    Pair with ``ESCAPE {_SQL_LIKE_ESCAPE_LITERAL}`` in the surrounding query.
    """
    escaped = (
        prefix.replace(_SQL_LIKE_ESCAPE, _SQL_LIKE_ESCAPE * 2)
        .replace("%", _SQL_LIKE_ESCAPE + "%")
        .replace("_", _SQL_LIKE_ESCAPE + "_")
    )
    return _sql_str(escaped + "/%")


_SQL_LIKE_ESCAPE_LITERAL = _sql_str(_SQL_LIKE_ESCAPE)


def _doc_chunk_entries(filename: str, doc: DocumentMetadata) -> dict[str, _ChunkEntry]:
    """Build chunk-metadata cache entries for a single document."""
    image_path = doc.original_path if doc.entry_kind == "image" else None
    return {
        _build_chunk_key(filename, i): _ChunkEntry(
            token_count=chunk.token_count,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            start_index=chunk.start_index,
            end_index=chunk.end_index,
            image_path=image_path,
        )
        for i, chunk in enumerate(doc.chunks)
    }


def _iter_doc_metadata(
    metadata_dir: Path,
) -> Iterator[tuple[str, DocumentMetadata]]:
    """Yield ``(entry_filename, doc)`` for every parseable metadata file.

    Metadata filenames use the stem-only convention (``report.json`` for
    ``report.md``), so the document extension is re-appended.
    """
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
        yield doc.description_path or doc_filename, doc


def _load_chunk_metadata(metadata_dir: Path) -> dict[str, _ChunkEntry]:
    """Build the per-chunk result-enrichment mapping from the metadata files."""
    metadata: dict[str, _ChunkEntry] = {}
    for entry_filename, doc in _iter_doc_metadata(metadata_dir):
        metadata.update(_doc_chunk_entries(entry_filename, doc))
    return metadata


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


@dataclass(slots=True)
class _RetrievalState:
    """Caches LanceDB storages and the embedding function.

    The lock guards ``_storage_cache`` and ``_embedding_func`` against
    concurrent first-access races.  Per-chunk metadata is not cached
    here — it is read from the on-disk metadata files when a search
    tool is built (see :func:`_load_chunk_metadata`).
    """

    _storage_cache: dict[str, cbrkit.indexable.lancedb[str]] = field(
        default_factory=dict
    )
    _embedding_func: (
        cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray] | None
    ) = field(default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock)

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
                self._embedding_func = cbrkit.sim.embed.openai(
                    model=cfg.model,
                    client=create_openai_client(
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
        """Return the LanceDB storage for *store*, lazily creating it."""
        key = store.store_key
        if key in self._storage_cache:
            return self._storage_cache[key]

        embedding_func = self.get_embedding_func()

        with self._lock:
            if key in self._storage_cache:
                return self._storage_cache[key]

            lancedb_dir = store.lancedb_dir(settings.data_dir)
            self._validate_fingerprint(key, lancedb_dir)

            storage: cbrkit.indexable.lancedb[str] = cbrkit.indexable.lancedb(
                uri=str(lancedb_dir),
                table_name=LANCEDB_TABLE,
                index_type="hybrid",
                conversion_func=embedding_func,
                metadata_func=lambda key, _: {
                    METADATA_FILENAME_COLUMN: _parse_chunk_key(key)[0]
                },
            )
            self._storage_cache[key] = storage
            return storage

    def _validate_fingerprint(self, store_key: str, lancedb_dir: Path) -> None:
        """Check the embedding fingerprint and wipe stale vector data.

        Must be called while ``self._lock`` is held.
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
            self._storage_cache.pop(store_key, None)
            self._embedding_func = None
            shutil.rmtree(lancedb_dir)
            lancedb_dir.mkdir(parents=True, exist_ok=True)
            metadata = {}

        if stored != current:
            metadata["embedding"] = current
            _write_store_metadata(lancedb_dir, metadata)

    def invalidate(self, store_key: str) -> None:
        """Drop cached state for *store_key* before its directories are wiped."""
        with self._lock:
            self._storage_cache.pop(store_key, None)


_state = _RetrievalState()


def _index_document_sync(store: Casebase, filename: str, doc: DocumentMetadata) -> None:
    storage = _state.get_storage(store)
    storage.replace_where(
        where=f"{METADATA_FILENAME_COLUMN} = {_sql_str(filename)}",
        data={
            _build_chunk_key(filename, i): chunk.text
            for i, chunk in enumerate(doc.chunks)
        },
    )


async def index_document(store: Casebase, filename: str, doc: DocumentMetadata) -> None:
    """Replace the index entries for a single document.

    Embeds the new chunks via the shared embedding function and writes
    them to LanceDB, removing any rows tied to the previous version of
    *filename*.
    """
    await asyncio.to_thread(_index_document_sync, store, filename, doc)


def _unindex_paths_sync(store: Casebase, filenames: Collection[str]) -> None:
    storage = _state.get_storage(store)
    quoted = ", ".join(_sql_str(name) for name in filenames)
    storage.delete_where(f"{METADATA_FILENAME_COLUMN} IN ({quoted})")


async def unindex_paths(store: Casebase, filenames: Collection[str]) -> None:
    """Remove chunks for the given document paths from the index."""
    if not filenames:
        return
    await asyncio.to_thread(_unindex_paths_sync, store, filenames)


def _unindex_subtree_sync(store: Casebase, prefix: str) -> None:
    storage = _state.get_storage(store)
    storage.delete_where(
        f"{METADATA_FILENAME_COLUMN} = {_sql_str(prefix)}"
        f" OR {METADATA_FILENAME_COLUMN} LIKE {_sql_like_pattern(prefix)}"
        f" ESCAPE {_SQL_LIKE_ESCAPE_LITERAL}"
    )


async def unindex_subtree(store: Casebase, prefix: str) -> None:
    """Remove every chunk whose path equals *prefix* or starts with ``prefix/``."""
    if not prefix:
        return
    await asyncio.to_thread(_unindex_subtree_sync, store, prefix)


async def invalidate_store(store: Casebase) -> None:
    """Drop cached state for *store* before its directories are wiped."""
    _state.invalidate(store.store_key)


def sync_index(store: Casebase) -> None:
    """Reconcile the LanceDB index with the metadata files on disk.

    cbrkit's :meth:`put_index` diffs against the existing table at the
    row level and only re-embeds changed/new chunks, so this is cheap
    enough to run on the periodic consistency tick without further
    optimisation.
    """
    storage = _state.get_storage(store)
    texts = {
        _build_chunk_key(entry_filename, i): chunk.text
        for entry_filename, doc in _iter_doc_metadata(
            store.metadata_path(settings.data_dir)
        )
        for i, chunk in enumerate(doc.chunks)
    }
    if texts or storage.has_index():
        logger.info(
            "Syncing LanceDB index for %s (%d chunks)", store.store_key, len(texts)
        )
        storage.put_index(texts)
    else:
        logger.debug("Skipping LanceDB index sync for empty store %s", store.store_key)


def build_search_tool(
    stores: Sequence[Casebase],
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> LanceDBSearchTool[RetrievedChunk]:
    """Build a :class:`LanceDBSearchTool` spanning one or more casebases.

    Per-chunk metadata is loaded fresh from each store's metadata
    directory so the result mapper can enrich :class:`SearchResult`
    objects without consulting an external cache.  The index is assumed
    up-to-date — every workspace mutation maintains it inline.
    """
    chunk_meta: dict[str, _ChunkEntry] = {}
    indexed: list[IndexedStorage] = []

    for store in stores:
        storage = _state.get_storage(store)
        prefix = store.prefix
        for key, entry in _load_chunk_metadata(
            store.metadata_path(settings.data_dir)
        ).items():
            chunk_meta[apply_prefix(prefix, key)] = entry

        file_filter = filter_for_store(store) if filter_for_store else None
        key_filter: Callable[[str], bool] | None = (
            _make_key_filter(file_filter) if file_filter is not None else None
        )
        indexed.append(
            IndexedStorage(storage=storage, prefix=prefix, filter_func=key_filter)
        )

    def _result_mapper(result: SearchResult) -> RetrievedChunk:
        return _to_retrieved_chunk(result, chunk_meta[result.key])

    return LanceDBSearchTool(
        storages=tuple(indexed),
        result_mapper=_result_mapper,
    )
