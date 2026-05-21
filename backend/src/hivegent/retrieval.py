"""Global LanceDB storage and retrieval using cbrkit.

The application uses one LanceDB table for all casebases.  Each row
carries two metadata columns — ``casebase_key`` and ``filename`` —
populated from the cbrkit key, so WHERE clauses stay simple and
per-casebase scoping is enforced inline at the query layer.

SQL is the source of truth for chunk metadata; the LanceDB index is
derived and rebuildable.  The search tool's result mapper loads only
the metadata it actually needs (one query per surviving result doc)
instead of preloading every chunk in every accessible casebase.

Public surface:

- :func:`build_search_tool` builds a search tool restricted to the
  casebases the caller can access.
- :func:`index_document` upserts the chunks of one document.
- :func:`unindex_paths` removes the chunks of one or more documents.
- :func:`unindex_subtree` removes every chunk whose path is at or
  beneath a prefix within a casebase.
- :func:`unindex_store` removes every chunk belonging to a casebase.
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
from .db import documents as db_documents
from .llm import create_openai_client
from .store import Casebase, lancedb_dir
from .tools.base import SearchPathFilterFunc, apply_prefix
from .tools.retrieval import LanceDBSearchTool, SearchResult

__all__ = [
    "build_search_tool",
    "index_document",
    "unindex_paths",
    "unindex_store",
    "unindex_subtree",
]

KEY_SEP = "::"
LANCEDB_TABLE = "chunks"
STORE_METADATA_FILE = "metadata.json"
CASEBASE_COLUMN = "casebase_key"
FILENAME_COLUMN = "filename"

logger = logging.getLogger(__name__)


# ─── Key encoding ──────────────────────────────────────────────────────


def _build_key(store: Casebase, filename: str, chunk_index: int) -> str:
    return f"{store.store_key}{KEY_SEP}{filename}{KEY_SEP}{chunk_index}"


def _parse_key(key: str) -> tuple[Casebase, str, int]:
    """Reverse of :func:`_build_key`."""
    try:
        store_key, rest = key.split(KEY_SEP, maxsplit=1)
        filename, index_str = rest.rsplit(KEY_SEP, maxsplit=1)
        return Casebase.from_store_key(store_key), filename, int(index_str)
    except ValueError as exc:
        raise ValueError(f"Invalid chunk key format: {key!r}") from exc


def _metadata_func(key: str, _text: str) -> dict[str, str]:
    """Populate metadata columns from the cbrkit key (called per row)."""
    store, filename, _idx = _parse_key(key)
    return {CASEBASE_COLUMN: store.store_key, FILENAME_COLUMN: filename}


# ─── WHERE-clause helpers ──────────────────────────────────────────────


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_SQL_LIKE_ESCAPE = "\\"


def _sql_like_subtree(prefix: str) -> str:
    """LIKE pattern matching everything strictly beneath ``prefix/``."""
    escaped = (
        prefix.replace(_SQL_LIKE_ESCAPE, _SQL_LIKE_ESCAPE * 2)
        .replace("%", _SQL_LIKE_ESCAPE + "%")
        .replace("_", _SQL_LIKE_ESCAPE + "_")
    )
    return _sql_str(escaped + "/%")


_SQL_LIKE_ESCAPE_LITERAL = _sql_str(_SQL_LIKE_ESCAPE)


def _where_store(store: Casebase) -> str:
    return f"{CASEBASE_COLUMN} = {_sql_str(store.store_key)}"


def _where_doc(store: Casebase, filename: str) -> str:
    return f"{_where_store(store)} AND {FILENAME_COLUMN} = {_sql_str(filename)}"


def _where_paths(store: Casebase, filenames: Collection[str]) -> str:
    quoted = ", ".join(_sql_str(name) for name in filenames)
    return f"{_where_store(store)} AND {FILENAME_COLUMN} IN ({quoted})"


def _where_subtree(store: Casebase, prefix: str) -> str:
    return (
        f"{_where_store(store)} AND ("
        f"{FILENAME_COLUMN} = {_sql_str(prefix)}"
        f" OR {FILENAME_COLUMN} LIKE {_sql_like_subtree(prefix)}"
        f" ESCAPE {_SQL_LIKE_ESCAPE_LITERAL})"
    )


# ─── Global fingerprint sidecar ────────────────────────────────────────


def _read_global_metadata() -> dict[str, Any]:
    """Read the global LanceDB sidecar, returning ``{}`` when absent."""
    path = lancedb_dir(settings.data_dir) / STORE_METADATA_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_global_metadata(data: dict[str, Any]) -> None:
    path = lancedb_dir(settings.data_dir) / STORE_METADATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─── Global storage state ─────────────────────────────────────────────


@dataclass(slots=True)
class _RetrievalState:
    """Caches the global LanceDB storage and the embedding function."""

    _storage: cbrkit.indexable.lancedb[str] | None = None
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

    def get_storage(self) -> cbrkit.indexable.lancedb[str]:
        """Return the global LanceDB storage, lazily creating it."""
        if self._storage is not None:
            return self._storage
        embedding_func = self.get_embedding_func()
        with self._lock:
            if self._storage is not None:
                return self._storage
            db_dir = lancedb_dir(settings.data_dir)
            self._validate_fingerprint(db_dir)
            self._storage = cbrkit.indexable.lancedb(
                uri=str(db_dir),
                table_name=LANCEDB_TABLE,
                index_type="hybrid",
                conversion_func=embedding_func,
                metadata_func=_metadata_func,
            )
            return self._storage

    def _validate_fingerprint(self, db_dir: Path) -> None:
        """Wipe the LanceDB directory when the embedding fingerprint changes.

        Must be called while ``self._lock`` is held.
        """
        metadata = _read_global_metadata()
        stored = metadata.get("embedding")
        current = settings.embedding.fingerprint()

        if stored is not None and stored != current:
            logger.warning(
                "Embedding config changed (was %s, now %s) — wiping LanceDB",
                stored, current,
            )
            self._storage = None
            self._embedding_func = None
            if db_dir.exists():
                shutil.rmtree(db_dir)
            db_dir.mkdir(parents=True, exist_ok=True)
            metadata = {}

        if stored != current:
            metadata["embedding"] = current
            _write_global_metadata(metadata)

    def invalidate(self) -> None:
        """Drop the cached storage handle (used after wiping the dir)."""
        with self._lock:
            self._storage = None


_state = _RetrievalState()


# ─── Mutators ─────────────────────────────────────────────────────────


def _index_document_sync(
    store: Casebase, filename: str, doc: DocumentMetadata
) -> None:
    storage = _state.get_storage()
    storage.replace_where(
        where=_where_doc(store, filename),
        data={
            _build_key(store, filename, i): chunk.text
            for i, chunk in enumerate(doc.chunks)
        },
    )


async def index_document(
    store: Casebase, filename: str, doc: DocumentMetadata
) -> None:
    """Replace the index entries for a single document.

    Embeds the new chunks and writes them to LanceDB, removing any rows
    tied to the previous version of *filename*.
    """
    await asyncio.to_thread(_index_document_sync, store, filename, doc)


def _unindex_paths_sync(store: Casebase, filenames: Collection[str]) -> None:
    storage = _state.get_storage()
    storage.delete_where(_where_paths(store, filenames))


async def unindex_paths(store: Casebase, filenames: Collection[str]) -> None:
    """Remove chunks for the given document paths from the index."""
    if not filenames:
        return
    await asyncio.to_thread(_unindex_paths_sync, store, filenames)


def _unindex_subtree_sync(store: Casebase, prefix: str) -> None:
    storage = _state.get_storage()
    storage.delete_where(_where_subtree(store, prefix))


async def unindex_subtree(store: Casebase, prefix: str) -> None:
    """Remove every chunk whose path equals *prefix* or starts with ``prefix/``."""
    if not prefix:
        return
    await asyncio.to_thread(_unindex_subtree_sync, store, prefix)


def _unindex_store_sync(store: Casebase) -> None:
    storage = _state.get_storage()
    storage.delete_where(_where_store(store))


async def unindex_store(store: Casebase) -> None:
    """Remove every chunk belonging to *store* from the global index."""
    await asyncio.to_thread(_unindex_store_sync, store)


# ─── Search-tool builder ──────────────────────────────────────────────


def build_search_tool(
    stores: Sequence[Casebase],
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> LanceDBSearchTool[RetrievedChunk]:
    """Build a search tool restricted to *stores*.

    The single global storage is paired with a key predicate that drops
    rows belonging to other casebases — and, optionally, files inside
    accessible casebases that fail a per-store filter.  Chunk-enrichment
    metadata is loaded from SQL inside the tool's result mapper,
    targeted at exactly the result keys.
    """
    allowed: dict[str, SearchPathFilterFunc] = {
        s.store_key: filter_for_store(s) if filter_for_store else None
        for s in stores
    }

    def key_filter(key: str) -> bool:
        try:
            store, filename, _idx = _parse_key(key)
        except ValueError:
            return False
        if store.store_key not in allowed:
            return False
        file_filter = allowed[store.store_key]
        return file_filter(filename) if file_filter is not None else True

    async def result_mapper(
        results: Sequence[SearchResult],
    ) -> list[RetrievedChunk]:
        if not results:
            return []
        by_doc: dict[tuple[Casebase, str], set[int]] = {}
        for r in results:
            try:
                store, filename, chunk_idx = _parse_key(r.key)
            except ValueError:
                continue
            by_doc.setdefault((store, filename), set()).add(chunk_idx)

        chunk_payload: dict[str, tuple[Casebase, str, int, Any]] = {}
        for (store, filename), needed_idx in by_doc.items():
            doc = await db_documents.get_document(store, filename)
            if doc is None:
                continue
            image_path = doc.original_path if doc.entry_kind == "image" else None
            for i, chunk in enumerate(doc.chunks):
                if i not in needed_idx:
                    continue
                chunk_payload[_build_key(store, filename, i)] = (
                    store, filename, i,
                    (chunk, image_path),
                )

        out: list[RetrievedChunk] = []
        for r in results:
            payload = chunk_payload.get(r.key)
            if payload is None:
                continue
            store, filename, chunk_idx, (chunk, image_path) = payload
            out.append(
                RetrievedChunk(
                    store_key=store.store_key,
                    filename=apply_prefix(store.prefix, filename),
                    chunk_index=chunk_idx,
                    text=r.text,
                    token_count=chunk.token_count,
                    score=round(r.score, 4),
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    start_index=chunk.start_index,
                    end_index=chunk.end_index,
                    image_path=image_path,
                )
            )
        return out

    return LanceDBSearchTool(
        storage=_state.get_storage(),
        filter_func=key_filter,
        result_mapper=result_mapper,
    )
