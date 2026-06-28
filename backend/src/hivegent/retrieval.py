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
from typing import cast

import cbrkit
import logfire
import sqlalchemy as sa
from cbrkit import filter as cbrkit_filter
from cbrkit.indexable import pgvector_async as PgvectorAsync
from cbrkit.typing import AsyncRetrieverFunc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .chunkers.base import ChunkData, RetrievedChunk
from .concurrency import shield_to_completion
from .config import settings
from .db import documents as db_documents
from .db.engine import session
from .db.models import Chunk, Document, IndexState
from .entries import description_path_for_stem, original_path_for_stem
from .http_client import get_http_client
from .llm import create_openai_client
from .store import Casebase
from .tools.base import SearchPathFilterFunc
from .tools.retrieval import SearchResult, VectorSearchTool

__all__ = [
    "build_search_tool",
    "index_document",
    "reconcile_index_state",
]

logger = logging.getLogger(__name__)


# ─── Global embedding + storage state ─────────────────────────────────


@dataclass(slots=True)
class _AsyncLazy[T]:
    """A value built once on first use, off the event loop.

    The builder runs in a worker thread (:func:`asyncio.to_thread`) so pulling
    model weights never blocks the loop, and a lock makes concurrent first
    callers share a single build. ``None`` is cached as a valid result (e.g. a
    disabled reranker); a build that raises is not cached, so the next call
    retries.
    """

    _build: Callable[[], T]
    _value: T | None = None
    _built: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get(self) -> T:
        """Return the cached value, building it once off the event loop."""
        if not self._built:
            async with self._lock:
                if not self._built:
                    self._value = await asyncio.to_thread(self._build)
                    self._built = True
        return cast(T, self._value)


def _build_reranker(device: str) -> AsyncRetrieverFunc[str, str, float] | None:
    """Construct the configured cbrkit reranker, or ``None`` when disabled.

    Loading a local cross-encoder pulls model weights, so callers build this
    off the event loop (see :class:`_AsyncLazy`).  ``device`` pins the
    cross-encoder; ``"auto"`` maps to ``None`` so it self-detects.
    """
    cfg = settings.rerank
    if not cfg.enabled:
        return None
    if cfg.provider == "sentence-transformers":
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(cfg.model, device=None if device == "auto" else device)
        return cbrkit.retrieval.rerank.cross_encoder(model=model)
    if not cfg.base_url:
        msg = "HTTP reranking requires HIVEGENT_RERANK__BASE_URL."
        raise ValueError(msg)
    return cbrkit.retrieval.rerank.http(
        model=cfg.model,
        url=f"{cfg.base_url.rstrip('/')}/rerank",
        client=get_http_client(allow_private=True),
        api_key=cfg.api_key or None,
        top_n=cfg.top_n,
    )


def _build_embedding_func(
    device: str,
) -> cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]:
    """Construct the configured embedding function.

    Loading a local sentence-transformers model pulls weights, so callers build
    this off the event loop (see :class:`_AsyncLazy`).  ``device`` pins the
    local model; ``"auto"`` maps to ``None`` so it self-detects (the remote
    OpenAI provider ignores it).
    """
    cfg = settings.embedding
    if cfg.provider == "openai":
        raw_func = cbrkit.sim.embed.openai(
            model=cfg.model,
            client=create_openai_client(
                api_key=cfg.api_key or None,
                base_url=cfg.base_url or None,
                allow_private_base_url=bool(cfg.base_url),
            ),
        )
    else:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(
            cfg.model, device=None if device == "auto" else device
        )
        raw_func = cbrkit.sim.embed.sentence_transformers(model=model)

    def instrumented(batches: Sequence[str]) -> Sequence[cbrkit.typing.NumpyArray]:
        with logfire.span("embed", batch_size=len(batches)):
            return raw_func(batches)

    return instrumented


@dataclass(slots=True)
class _RetrievalState:
    """Caches the global embedding function, storage handle, and reranker.

    ``device`` pins the local embedding/reranker models; it defaults to
    ``"auto"`` and is a code-level knob (see :data:`_state`) rather than a
    settings field, mirroring the model-based converters and chunkers.
    """

    device: str = "auto"
    _storage: PgvectorAsync[str, Chunk] | None = None
    _embedding: _AsyncLazy[
        cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]
    ] = field(init=False)
    _reranker: _AsyncLazy[AsyncRetrieverFunc[str, str, float] | None] = field(
        init=False
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._embedding = _AsyncLazy(lambda: _build_embedding_func(self.device))
        self._reranker = _AsyncLazy(lambda: _build_reranker(self.device))

    async def get_reranker(self) -> AsyncRetrieverFunc[str, str, float] | None:
        """Return the cached reranker, building it once off the event loop."""
        return await self._reranker.get()

    async def get_embedding_func(
        self,
    ) -> cbrkit.typing.BatchConversionFunc[str, cbrkit.typing.NumpyArray]:
        """Return the cached embedding function, building it once off the event loop."""
        return await self._embedding.get()

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
                conversion_func=await self.get_embedding_func(),
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

    await shield_to_completion(storage.replace_where(where, data))


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
    original_suffix: str | None
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
                Document.original_suffix,
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
            original_suffix=original_suffix,
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
            original_suffix,
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
                original_path_for_stem(row.stem_path, row.original_suffix)
                if row.entry_kind == "image"
                else None
            )
            by_key[row.key] = RetrievedChunk(
                store_key=row.store_key,
                filename=store.scope.render(filename),
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
        reranker_factory=_state.get_reranker,
        candidate_multiplier=settings.rerank.candidate_multiplier,
    )
