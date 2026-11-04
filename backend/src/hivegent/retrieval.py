"""Embedding + vector search over the global ``chunks`` table.

Hivegent owns the ``chunks`` table schema as a declarative ORM class
in :mod:`hivegent.db.models`; cbrkit reads via
:meth:`pgvector_async(model=Chunk)`.  Writes go through
cbrkit's :meth:`storage.replace_where` with full row mappings
(``id -> {text, document_id, idx, ...}``); cbrkit derives the
``embedding`` from the ``text`` column and Postgres fills ``tsv``, while
hivegent stays in charge of the schema.  cbrkit embeds the batch before it
opens the write transaction, so DELETE and INSERT are all a pooled connection
is ever held for.

Public surface:

- :func:`index_document` chunk-indexes one document.  Calls cbrkit's
  ``replace_where`` to atomically delete and re-insert that document's
  chunk rows with fresh embeddings.
- :func:`build_search_tool` builds a search tool scoped to the
  casebases the caller can access.
- :func:`reconcile_index_state` resizes the vector column and re-embeds
  in place when the configured embedding model changes.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import cast

import cbrkit
import logfire
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cbrkit import filter as cbrkit_filter
from cbrkit.indexable import pgvector_async as PgvectorAsync
from cbrkit.typing import AsyncRetrieverFunc
from sqlalchemy import Connection

from .chunkers.base import ChunkData, RetrievedChunk
from .concurrency import shield_to_completion
from .config import reveal, settings
from .converters.base import is_image_suffix
from .db import documents as db_documents
from .db.engine import session
from .db.models import Chunk, Document, IndexState
from .entries import description_path_for_stem, original_path_for_stem
from .http_client import get_trusted_http_client
from .llm import create_openai_client
from .store import Casebase
from .tools.retrieval import SearchResult, VectorSearchTool
from .types import DocumentFilter

__all__ = [
    "build_search_tool",
    "index_document",
    "reconcile_index_state",
    "warm_index",
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
        client=get_trusted_http_client(),
        api_key=reveal(cfg.api_key),
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
                api_key=reveal(cfg.api_key),
                base_url=cfg.base_url or None,
                base_url_is_trusted=True,
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
                key_column=Chunk.id.key,
                value_column=Chunk.text.key,
                pgvector_column=Chunk.embedding.key,
                tsvector_column=Chunk.tsv.key,
                tsvector_config=settings.embedding.text_search_config,
            )
            return self._storage


_state = _RetrievalState()


async def warm_index() -> None:
    """Build the embedding model and storage handle ahead of the first upload.

    The embedding model loads lazily on first use and its build (loading the
    sentence-transformers weights off the event loop) costs seconds; without a
    warm-up the very first upload after boot silently pays that latency mid-run.
    Called from the lifespan so the models are resident before any request lands.
    """
    await _state.get_storage()


async def index_document(document_id: str, chunks: Sequence[ChunkData]) -> None:
    """Atomically replace *document_id*'s chunk rows with *chunks*.

    Each chunk is passed to cbrkit as a :class:`Chunk` instance keyed by
    its auto-generated id; cbrkit derives the ``embedding`` from the
    row's ``text`` via the storage's ``conversion_func`` (off the event loop,
    and before the write transaction opens) and Postgres generates ``tsv``.
    The cbrkit PK (``chunks.id``) is a generated id — the real chunk identity is
    the UNIQUE ``(document_id, idx)`` pair, kept normalised on its own columns.

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


# Through ``metadata``, since ``Chunk.__table__`` is typed as a bare ``FromClause``.
_CHUNKS = Chunk.metadata.tables[Chunk.__tablename__]
_EMBEDDING = _CHUNKS.c.embedding
_EMBEDDING_INDEX = next(
    index for index in _CHUNKS.indexes if index.columns.contains_column(_EMBEDDING)
)


@asynccontextmanager
async def _index_state_lock() -> AsyncGenerator[None]:
    """Hold a session-level advisory lock so booting replicas reconcile in turn.

    The connection autocommits, so it holds no transaction open, and with it
    no snapshot that would keep vacuum from the rows a rebuild rewrites.
    """
    from .db.engine import get_engine

    key = sa.func.hashtextextended(IndexState.__tablename__, 0)
    async with get_engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(sa.select(sa.func.pg_advisory_lock(key)))
        try:
            yield
        finally:
            await conn.execute(sa.select(sa.func.pg_advisory_unlock(key)))


def _schema_ops(conn: Connection) -> Operations:
    """Alembic's schema operations, run at boot outside a migration."""
    return Operations(MigrationContext.configure(conn))


def _fit_embedding_column(conn: Connection) -> bool:
    """Retype the live embedding column to the one the ORM declares.

    Vectors cannot be cast across dimensions, so a resize swaps in an empty,
    nullable column, which changes only the catalog and takes the HNSW index
    along.  It stays nullable until a re-embed fills it.

    Returns:
        Whether the column holds a complete set of vectors.
    """
    live = next(
        column
        for column in sa.inspect(conn).get_columns(_CHUNKS.name)
        if column["name"] == _EMBEDDING.name
    )
    live_type = live["type"].compile(conn.dialect)
    declared_type = _EMBEDDING.type.compile(conn.dialect)
    if live_type == declared_type:
        return not live["nullable"]

    logger.warning("Resizing %s from %s to %s", _EMBEDDING, live_type, declared_type)
    ops = _schema_ops(conn)
    ops.drop_column(_CHUNKS.name, _EMBEDDING.name)
    ops.add_column(_CHUNKS.name, sa.Column(_EMBEDDING.name, _EMBEDDING.type))

    return False


def _complete_embedding_column(conn: Connection) -> None:
    """Restore ``NOT NULL`` and build the HNSW index once over the filled column."""
    _schema_ops(conn).alter_column(_CHUNKS.name, _EMBEDDING.name, nullable=False)
    _EMBEDDING_INDEX.create(conn, checkfirst=True)


async def reconcile_index_state() -> None:
    """Fit the vector index to the configured embedding model before serving.

    A changed dimension resizes the embedding column (see
    :func:`_fit_embedding_column`), and that or a changed ``(provider,
    model)`` fingerprint re-embeds every row in place.  The HNSW index is
    dropped for the rebuild and built once over the loaded vectors, which
    pgvector recommends over updating through the index.

    A nullable column marks an unfinished resize and the fingerprint is
    committed only after the rebuild, so a crash at any point makes the next
    boot retry.  ``reembed_all`` commits page by page, so a torn rebuild mixes
    vectors from two models.  What makes that acceptable is the lifespan
    awaiting this *before* the server serves.  Move this call off the startup
    path and it must become ``reembed_all(atomic=True)``, which buffers every
    new vector in memory.
    """
    current = settings.embedding.fingerprint()
    async with _index_state_lock():
        async with session() as s:
            conn = await s.connection()
            complete = await conn.run_sync(_fit_embedding_column)
            row = (await s.execute(sa.select(IndexState))).scalar_one_or_none()
            if row is None:
                row = IndexState(
                    embedding_provider=current["provider"],
                    embedding_model=current["model"],
                )
                s.add(row)

            previous = {"provider": row.embedding_provider, "model": row.embedding_model}
            if complete and previous == current:
                return

            await conn.run_sync(_EMBEDDING_INDEX.drop, checkfirst=True)

        logger.warning(
            "Embedding config changed (was %s, now %s), re-embedding in place",
            previous,
            current,
        )
        storage = await _state.get_storage()
        await storage.reembed_all()
        async with session() as s:
            await (await s.connection()).run_sync(_complete_embedding_column)
            row = (await s.execute(sa.select(IndexState))).scalar_one()
            row.embedding_provider = current["provider"]
            row.embedding_model = current["model"]


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
    store_key: str


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
            store_key=Casebase.for_owner(owner_user_id, owner_group_id).store_key,
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
            owner_user_id,
            owner_group_id,
        ) in rows
    ]


def build_search_tool(
    stores: Sequence[Casebase],
    *,
    filter_for_store: Callable[[Casebase], DocumentFilter | None] | None = None,
) -> VectorSearchTool[RetrievedChunk]:
    """Build a search tool restricted to *stores*.

    Scoping is enforced entirely as a pre-filter at the SQL level: the set of
    document ids owned by *stores* and admitted by *filter_for_store* is
    resolved before the cbrkit query, then passed as an
    ``In("document_id", ...)`` filter so the HNSW/FTS scan never visits a row
    the caller cannot see — neither one owned by another store nor one the
    conversation's document scope excludes.  The ids are resolved per call, so
    a scope the user narrows between turns takes effect on the next search.
    """
    store_index = {s.store_key: s for s in stores}
    document_filters = (
        {store: filter_for_store(store) for store in stores} if filter_for_store else {}
    )

    async def resolve_filter() -> cbrkit_filter.Filter | None:
        doc_ids = await db_documents.resolve_accessible_document_ids(
            stores, filters=document_filters
        )
        if not doc_ids:
            return cbrkit_filter.Eq("document_id", "")  # match nothing
        return cbrkit_filter.In("document_id", doc_ids)

    # TODO(cbrkit): scope by a subquery on the owners instead of the ids, so a
    # search no longer carries one parameter per accessible document, which
    # also fails beyond PostgreSQL's 65535 parameters.  It needs cbrkit's
    # `compile_filter` (`cbrkit/indexable/sqlalchemy.py`) to take a SQLAlchemy
    # expression as is, as a new filter node such as `Sql(clause:
    # sa.ColumnElement[bool])`, since `Raw` only takes a string it parses with
    # `sa.text`, which binds nothing.  The scope is prepared in
    # `db.documents.chunk_scope` and `db.documents.accessible_chunks`.
    #
    # async def resolve_filter() -> cbrkit_filter.Filter | None:
    #     return cbrkit_filter.Sql(
    #         await db_documents.accessible_chunks(stores, filters=document_filters)
    #     )

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
            image_path = (
                original_path_for_stem(row.stem_path, row.original_suffix)
                if is_image_suffix(row.original_suffix or "")
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
