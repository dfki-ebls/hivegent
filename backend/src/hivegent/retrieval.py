"""Embedding + vector search over the global ``chunks`` table.

Hivegent owns the ``chunks`` table schema as a declarative ORM class
in :mod:`hivegent.db.models`; cbrkit reads via
:meth:`pgvector_async(model=Chunk)`.  Writes go through
cbrkit's :meth:`storage.replace_where` with full row mappings
(``id -> {text, document_id, idx, ...}``); cbrkit derives the
``embedding`` from the ``text`` column and Postgres fills ``tsv``, so
embedding, INSERT, and DELETE happen in one transaction while hivegent
stays in charge of the schema.

Public surface:

- :func:`index_document` chunk-indexes one document.  Calls cbrkit's
  ``replace_where`` to atomically delete and re-insert that document's
  chunk rows with fresh embeddings.
- :func:`build_search_tool` builds a search tool scoped to the
  casebases the caller can access.
- :func:`reconcile_index_state` re-embeds in place when the configured
  embedding model differs from the persisted fingerprint.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import cbrkit
import logfire
import sqlalchemy as sa
from cbrkit import filter as cbrkit_filter
from cbrkit.indexable import pgvector_async as PgvectorAsync
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .chunkers.base import ChunkData, RetrievedChunk
from .config import settings
from .db import documents as db_documents
from .db.engine import session
from .db.models import Chunk, Document, IndexState
from .entries import description_path_for_stem, original_path_for_stem
from .llm import create_openai_client
from .store import Casebase
from .tools.base import SearchPathFilterFunc, apply_prefix
from .tools.retrieval import SearchResult, VectorSearchTool


__all__ = [
    "build_search_tool",
    "index_document",
    "reconcile_index_state",
]

logger = logging.getLogger(__name__)


# ─── Global embedding + storage state ─────────────────────────────────


@dataclass(slots=True)
class _RetrievalState:
    """Caches the global embedding function and pgvector storage handle."""

    _storage: PgvectorAsync[str, Chunk] | None = None
    _embedding_func: (
        cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray] | None
    ) = field(default=None)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get_embedding_func(
        self,
    ) -> cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]:
        """Build or return the shared embedding function based on settings."""
        if self._embedding_func is not None:
            return self._embedding_func
        cfg = settings.embedding
        if cfg.provider == "openai":
            raw_func = cbrkit.sim.embed.openai(
                model=cfg.model,
                client=create_openai_client(
                    api_key=cfg.api_key or None,
                    base_url=cfg.base_url or None,
                ),
            )
        else:
            raw_func = cbrkit.sim.embed.sentence_transformers(model=cfg.model)

        def instrumented(
            batches: Sequence[str],
        ) -> Sequence[cbrkit.typing.NumpyArray]:
            with logfire.span("embed", batch_size=len(batches)):
                return raw_func(batches)

        self._embedding_func = instrumented
        return self._embedding_func

    async def get_storage(self) -> PgvectorAsync[str, Chunk]:
        """Return the cbrkit storage handle, lazily wiring it up."""
        if self._storage is not None:
            return self._storage
        async with self._lock:
            if self._storage is not None:
                return self._storage
            from .db.engine import get_engine

            self._storage = PgvectorAsync[str, Chunk](
                engine=get_engine(),
                model=Chunk,
                conversion_func=self.get_embedding_func(),
                index_type="hybrid",
                key_type="str",
                key_column="id",
                value_column="text",
                pgvector_column="embedding",
                tsvector_column="tsv",
                tsvector_config=settings.embedding.text_search_config,
            )
            return self._storage


_state = _RetrievalState()


async def index_document(document_id: str, chunks: Sequence[ChunkData]) -> None:
    """Atomically replace *document_id*'s chunk rows with *chunks*.

    Each chunk is passed to cbrkit as a :class:`Chunk` instance keyed by
    its auto-generated nanoid; cbrkit derives the ``embedding`` from the
    row's ``text`` via the storage's ``conversion_func`` and Postgres
    generates ``tsv``, so embedding, INSERT, and DELETE all run inside
    ``replace_where``.  The cbrkit PK (``chunks.id``) is the nanoid —
    the real chunk identity is the UNIQUE ``(document_id, idx)`` pair,
    kept normalised on its own columns.

    Empty *chunks* just deletes any existing rows for the document.
    """
    storage = await _state.get_storage()
    where = cbrkit_filter.Eq("document_id", document_id)
    if not chunks:
        await storage.delete_where(where)
        return

    rows = [
        Chunk(
            text=c.text,
            document_id=document_id,
            idx=i,
            token_count=c.token_count,
            start_index=c.start_index,
            end_index=c.end_index,
            start_line=c.start_line,
            end_line=c.end_line,
        )
        for i, c in enumerate(chunks)
    ]
    data = {row.id: row for row in rows}

    await asyncio.shield(storage.replace_where(where, data))


async def reconcile_index_state() -> None:
    """Drive an in-place ``reembed_all`` when the embedding model changes.

    Idempotent: on a clean install inserts the singleton
    :class:`IndexState` row; subsequent calls only act when the
    ``(provider, model)`` pair differs from the persisted one.  The new
    fingerprint is committed only *after* the rebuild completes, so a
    crash mid-rebuild leaves the previous fingerprint and the next
    boot retries.
    """
    current = settings.embedding.fingerprint()
    async with session() as s:
        row = (await s.execute(sa.select(IndexState))).scalar_one_or_none()
        if row is None:
            # Idempotent: a concurrent fresh boot may insert the singleton
            # between our SELECT and this INSERT.  DO NOTHING means both
            # bootstrappers settle on the same fingerprint without reembed.
            await s.execute(
                pg_insert(IndexState)
                .values(
                    id=1,
                    embedding_provider=current["provider"],
                    embedding_model=current["model"],
                )
                .on_conflict_do_nothing()
            )
            return
        previous = {
            "provider": row.embedding_provider,
            "model": row.embedding_model,
        }
        if previous == current:
            return

    logger.warning(
        "Embedding config changed (was %s, now %s) — re-embedding in place",
        previous,
        current,
    )
    storage = await _state.get_storage()
    await storage.reembed_all()
    async with session() as s:
        row = (await s.execute(sa.select(IndexState))).scalar_one()
        row.embedding_provider = current["provider"]
        row.embedding_model = current["model"]
        row.fingerprint_set_at = datetime.now(UTC)


# ─── Search-tool builder ──────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class _EnrichedRow:
    """One row joined from chunks + documents at search-time enrichment."""

    key: str
    idx: int
    token_count: int
    start_index: int
    end_index: int
    start_line: int
    end_line: int
    text: str
    stem_path: str
    original_ext: str | None
    entry_kind: str
    store_key: str


def _store_key_for(owner_user_id: str | None, owner_group_id: str | None) -> str:
    if owner_user_id is not None:
        return Casebase.for_user(owner_user_id).store_key
    assert owner_group_id is not None
    return Casebase.for_group(owner_group_id).store_key


async def _load_enriched(keys: Sequence[str]) -> list[_EnrichedRow]:
    """Load chunk + document metadata for the keys in one JOIN query."""
    async with session() as s:
        stmt = (
            sa.select(
                Chunk.id,
                Chunk.idx,
                Chunk.token_count,
                Chunk.start_index,
                Chunk.end_index,
                Chunk.start_line,
                Chunk.end_line,
                Chunk.text,
                Document.stem_path,
                Document.original_ext,
                Document.entry_kind,
                Document.owner_user_id,
                Document.owner_group_id,
            )
            .select_from(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_(list(keys)))
        )
        rows = (await s.execute(stmt)).all()
    return [
        _EnrichedRow(
            key=key,
            idx=idx,
            token_count=token_count,
            start_index=start_index,
            end_index=end_index,
            start_line=start_line,
            end_line=end_line,
            text=text,
            stem_path=stem_path,
            original_ext=original_ext,
            entry_kind=entry_kind.value,
            store_key=_store_key_for(owner_user_id, owner_group_id),
        )
        for (
            key,
            idx,
            token_count,
            start_index,
            end_index,
            start_line,
            end_line,
            text,
            stem_path,
            original_ext,
            entry_kind,
            owner_user_id,
            owner_group_id,
        ) in rows
    ]


def build_search_tool(
    stores: Sequence[Casebase],
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> VectorSearchTool[RetrievedChunk]:
    """Build a search tool restricted to *stores*.

    Per-store scoping is enforced as a pre-filter at the SQL level: the
    set of document ids owned by *stores* is resolved before the cbrkit
    query, then passed as an ``In("document_id", ...)`` filter so the
    HNSW/FTS scan never visits rows the caller cannot see.  The
    optional *filter_for_store* prunes further per-file inside the
    result mapper after the JOIN has loaded the filename.
    """
    store_index = {s.store_key: s for s in stores}
    file_filters = {
        s.store_key: filter_for_store(s) if filter_for_store else None for s in stores
    }

    async def resolve_filter() -> cbrkit_filter.Filter | None:
        doc_ids = await db_documents.resolve_accessible_document_ids(stores)
        if not doc_ids:
            return cbrkit_filter.Eq("document_id", "")  # match nothing
        return cbrkit_filter.In("document_id", doc_ids)

    async def enrich(results: Sequence[SearchResult]) -> list[RetrievedChunk]:
        if not results:
            return []
        rows = await _load_enriched([r.key for r in results])
        score_by_key = {r.key: r.score for r in results}
        by_key: dict[str, RetrievedChunk] = {}
        for row in rows:
            store = store_index.get(row.store_key)
            if store is None:
                continue
            filename = description_path_for_stem(row.stem_path)
            file_filter = file_filters.get(row.store_key)
            if file_filter is not None and not file_filter(filename):
                continue
            image_path = (
                original_path_for_stem(row.stem_path, row.original_ext)
                if row.entry_kind == "image"
                else None
            )
            by_key[row.key] = RetrievedChunk(
                store_key=row.store_key,
                filename=apply_prefix(store.prefix, filename),
                chunk_index=row.idx,
                text=row.text,
                token_count=row.token_count,
                score=round(score_by_key.get(row.key, 0.0), 4),
                start_line=row.start_line,
                end_line=row.end_line,
                start_index=row.start_index,
                end_index=row.end_index,
                image_path=image_path,
            )
        return [by_key[r.key] for r in results if r.key in by_key]

    return VectorSearchTool(
        storage_factory=_state.get_storage,
        filter_factory=resolve_filter,
        result_mapper=enrich,
    )
