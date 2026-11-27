"""Embedding + vector search over the global ``chunks`` table.

Hivegent owns the ``chunks`` table schema as a declarative ORM class
in :mod:`hivegent.db.models`, managed by Alembic, and cbrkit reads it via
:class:`~cbrkit.indexable.Pgvector` with ``model=Chunk``.  Writes go through
cbrkit's :meth:`storage.replace_where` with full row mappings
(``id -> {text, document_id, idx, ...}``), cbrkit derives the
``embedding`` from the ``text`` column and Postgres fills ``tsv``, while
hivegent stays in charge of the schema.  cbrkit embeds the batch before it
opens the write transaction, so DELETE and INSERT are all a pooled connection
is ever held for.

Public surface:

- :func:`index_document` chunk-indexes one document.  Calls cbrkit's
  ``replace_where`` to atomically delete and re-insert that document's
  chunk rows with fresh embeddings.
- :func:`document_memory` is the `exar.typing.Memory` of the chunks a
  caller may read, an `exar.cbrkit.IndexMemory` over the chunk index scoped
  to them (:func:`chunk_memory`), so the search tool, experience, and
  retrieval metrics search the same scope.
- :func:`build_search_tool` builds a search tool scoped to the
  workspaces the caller can access.
- :func:`reconcile_index_state` fits the index columns to the declared
  model and re-embeds in place when the configured embedding model changes.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

import httpx2
import logfire
import sqlalchemy as sa
from cbrkit.indexable import Pgvector, PgvectorRetriever, Search, Unscoped
from cbrkit.retrieve.rerank import HTTP, Rerank
from cbrkit.sim.embed import PydanticAI
from cbrkit.typing import AsyncCbrFunc, EmbeddingFunc, NumpyArray, RerankFunc
from exar import ingest
from exar.cbrkit import IndexMemory
from exar.helpers import shield_to_completion
from exar.ingest import limit_threads
from exar.memory import GatedMemory
from exar.policy import EffectPolicy
from exar.typing import Hit
from openai import AsyncOpenAI
from pydantic_ai.embeddings import Embedder
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.providers import Provider

from .config import reveal, settings
from .config.llm import LlmProviderSettings
from .config.retrieval import (
    LocalEmbeddingSettings,
    LocalRerankSettings,
    RemoteEmbeddingSettings,
    RemoteRerankSettings,
)
from .db import documents as db_documents
from .db.engine import session
from .db.models import Chunk, Document
from .http_client import get_trusted_http_client
from .llm import create_openai_provider
from .reach import Reach
from .security import OperatorUrl
from .shared.formats import is_image_suffix
from .tools.base import ToolFunc
from .tools.entries import Part
from .tools.retrieval import SEARCHES, RetrievedChunk, SearchType, search

__all__ = [
    "build_search_tool",
    "chunk_memory",
    "document_memory",
    "embedding_func",
    "index_document",
    "reconcile_index_state",
    "warm_index",
]

logger = logging.getLogger(__name__)


# ─── Global embedding + storage state ─────────────────────────────────


@dataclass(slots=True)
class _AsyncLazy[T]:
    """A value built once on first use, off the event loop.

    The builder runs in a worker thread (:func:`asyncio.to_thread`) so importing
    torch never blocks the loop, and concurrent first callers share its task,
    shielded from the cancellation of any one of them.  A build that raised or
    was cancelled is not cached, so the next call retries.
    """

    _build: Callable[[], T]
    _task: asyncio.Task[T] | None = None

    async def get(self) -> T:
        """Return the cached value, building it once off the event loop."""
        task = self._task

        if task is None or (
            task.done() and (task.cancelled() or task.exception() is not None)
        ):
            task = self._task = asyncio.create_task(asyncio.to_thread(self._build))

        return await asyncio.shield(task)


def _reranker(
    cfg: LocalRerankSettings | RemoteRerankSettings | None,
    providers: Mapping[str, LlmProviderSettings],
    client: Callable[[OperatorUrl], httpx2.AsyncClient] = get_trusted_http_client,
) -> Rerank | None:
    """The reranker *cfg* configures, ``None`` when disabled.

    A remote one borrows the provider of its endpoint from *providers*, and
    reaches it through the *client* of its URL.
    """
    if cfg is None:
        return None

    model: RerankFunc

    if isinstance(cfg, LocalRerankSettings):
        # Imported on use, since sentence-transformers imports torch.
        from cbrkit.retrieve.rerank import CrossEncoder

        limit_threads(settings.compute.threads_per_worker)
        model = CrossEncoder(model=cfg.model, device=settings.compute.device)
    else:
        provider = providers[cfg.endpoint]
        model = HTTP(
            model=cfg.model,
            url=provider.url("rerank"),
            client=client(provider.base_url),
            api_key=reveal(provider.api_key),
        )

    return Rerank(model=model, limit=cfg.top_n)


def _prefixed(
    func: EmbeddingFunc[str, NumpyArray], prefix: str
) -> EmbeddingFunc[str, NumpyArray]:
    """*func* embedding every text behind *prefix*, traced per batch."""

    async def embed(values: Sequence[str], /) -> Sequence[NumpyArray]:
        with logfire.span("embed", batch_size=len(values)):
            return await func([prefix + value for value in values])

    return embed


def _embedders(
    cfg: LocalEmbeddingSettings | RemoteEmbeddingSettings,
    providers: Mapping[str, LlmProviderSettings],
    create_provider: Callable[
        [LlmProviderSettings], Provider[AsyncOpenAI]
    ] = create_openai_provider,
) -> tuple[EmbeddingFunc[str, NumpyArray], EmbeddingFunc[str, NumpyArray]]:
    """The embedding model *cfg* configures, of documents and of queries.

    Both share one model, so a local model loads its weights once, on the
    first batch.  A remote one borrows the provider of its endpoint from
    *providers*, which *create_provider* connects.
    """
    if isinstance(cfg, RemoteEmbeddingSettings):
        model = OpenAIEmbeddingModel(
            cfg.model, provider=create_provider(providers[cfg.endpoint])
        )
        documents: EmbeddingFunc[str, NumpyArray] = PydanticAI(
            Embedder(model),
            input_type="document",
            batch_size=cfg.batch_size,
            concurrency=cfg.concurrency,
            settings=cfg.request,
        )
        queries: EmbeddingFunc[str, NumpyArray] = replace(documents, input_type="query")
    else:
        # Imported on use, since sentence-transformers imports torch.
        from cbrkit.sim.embed import SentenceTransformers

        limit_threads(settings.compute.threads_per_worker)
        documents = queries = SentenceTransformers(
            model=cfg.model,
            batch_size=cfg.batch_size,
            device=settings.compute.device,
        )

    return _prefixed(documents, cfg.document_prefix), _prefixed(
        queries, cfg.query_prefix
    )


@dataclass(slots=True, frozen=True, kw_only=True)
class _Index:
    """The chunk index: its embedding models, its indexer, and the reranker of its searches.

    The indexer reaches every chunk, so only indexing and re-embedding hold
    it, and every read narrows it to a :class:`~hivegent.reach.Reach` first
    (:func:`document_memory`).
    """

    embed: EmbeddingFunc[str, NumpyArray]
    embed_query: EmbeddingFunc[str, NumpyArray]
    indexer: Pgvector[UUID, Chunk]
    reranker: Rerank | None


def _build_index() -> _Index:
    """Wire up the index.  Its table is Alembic's, so the storage creates nothing."""
    from .db.engine import get_engine

    embed, embed_query = _embedders(settings.embedding, settings.llm.providers)

    return _Index(
        embed=embed,
        embed_query=embed_query,
        indexer=Pgvector[UUID, Chunk](
            engine=get_engine(),
            model=Chunk,
            embedder=embed,
            index_type="hybrid",
            value_column=Chunk.text.key,
            vector_column=Chunk.embedding.key,
            tsvector_column=Chunk.tsv.key,
            tsvector_config=settings.embedding.text_search_config,
            scope=Unscoped.TABLE,
        ),
        reranker=_reranker(settings.rerank, settings.llm.providers),
    )


_INDEX = _AsyncLazy(_build_index)


async def _indexer() -> Pgvector[UUID, Chunk]:
    return (await _INDEX.get()).indexer


async def embedding_func() -> EmbeddingFunc[str, NumpyArray]:
    """The document embedding model of the index, built once off the event loop."""
    return (await _INDEX.get()).embed


async def warm_index() -> None:
    """Load the local embedding and rerank models ahead of the first upload and search.

    Their weights load on the first call and cost seconds, which the very first
    upload or search after boot would otherwise pay mid-run.  Called from the
    lifespan so the models are resident before any request lands.  A remote
    model is not called, so an unreachable endpoint does not fail the boot.
    """
    await _warm_up(await _INDEX.get(), settings.embedding, settings.rerank)


async def _warm_up(
    index: _Index,
    embedding: LocalEmbeddingSettings | RemoteEmbeddingSettings,
    rerank: LocalRerankSettings | RemoteRerankSettings | None,
) -> None:
    """Call the models of *index* that *embedding* and *rerank* run locally."""
    warm_ups: list[Awaitable[object]] = []

    if isinstance(embedding, LocalEmbeddingSettings):
        warm_ups.append(index.embed(["warm-up"]))

    if index.reranker is not None and isinstance(rerank, LocalRerankSettings):
        warm_ups.append(index.reranker.model("warm-up", ["warm-up"]))

    _ = await asyncio.gather(*warm_ups)


async def index_document(
    document_id: UUID,
    chunks: Sequence[ingest.Chunk],
    *,
    storage: Pgvector[UUID, Chunk] | None = None,
) -> None:
    """Atomically replace *document_id*'s chunk rows with *chunks* in *storage*, the index's by default.

    Each chunk is passed to cbrkit as a :class:`Chunk` row keyed by its
    generated id, cbrkit derives the ``embedding`` from the row's ``text``
    before the write transaction opens, and Postgres generates ``tsv``.
    The real chunk identity is the UNIQUE ``(document_id, idx)`` pair.

    Empty *chunks* just deletes any existing rows for the document.
    """
    storage = storage or await _indexer()
    where = Chunk.document_id == document_id

    if not chunks:
        _ = await storage.delete_where(where)
        return

    rows = [
        Chunk(
            text=c.text,
            document_id=document_id,
            idx=i,
            token_count=c.tokens,
            start_index=c.start,
            end_index=c.end,
            start_line=c.start_line,
            end_line=c.end_line,
        )
        for i, c in enumerate(chunks)
    ]

    _ = await shield_to_completion(
        storage.replace_where(where, {row.id: row for row in rows})
    )


async def reconcile_index_state() -> None:
    """Fit the index to the configured embedding model and full-text search before serving.

    cbrkit's ``Pgvector.reconcile`` swaps an embedding column of another
    dimension, recreates a ``tsv`` column of other configurations, and re-embeds
    every row in place once the fingerprint of the stored vectors changed.
    The fingerprint names what defines them, not where the model is served nor
    the query prefix, which no stored vector holds, and changing its format
    re-embeds once.  The rebuild commits page by page, which is acceptable only
    because the lifespan awaits this before the server serves.
    """
    cfg = settings.embedding
    fingerprint = json.dumps(
        {
            "provider": cfg.provider,
            "model": cfg.model,
            "document_prefix": cfg.document_prefix,
        },
        sort_keys=True,
    )

    if await (await _indexer()).reconcile(fingerprint):
        logger.warning("Re-embedded every chunk for %s", fingerprint)


# ─── Search ───────────────────────────────────────────────────────────


def chunk_memory(
    storage: Pgvector[UUID, Chunk],
    *,
    search: Search,
    query_embedder: EmbeddingFunc[str, NumpyArray] | None = None,
    reranker: AsyncCbrFunc[UUID, str, float] | None = None,
    candidate_multiplier: int = 1,
    retriever: Callable[..., AsyncCbrFunc[UUID, str, float]] = PgvectorRetriever,
) -> GatedMemory[UUID, Chunk]:
    """The chunks of *storage*, its scope included, as an `exar.typing.Memory` of chunk rows by chunk id.

    Chunks are written by indexing their documents (:func:`index_document`),
    so the memory refuses writes.

    Args:
        storage: The chunk index, whose scope admits the chunks, which the
            database evaluates per search and read, so it sees fresh data
            (e.g. a document moved out of reach) and the scan never visits a
            row outside the scope.
        search: Dense, sparse, or hybrid search.
        query_embedder: Embeds the queries, the embedder of *storage* if None.
        reranker: Rescores the candidates of a search, none if None.
        candidate_multiplier: How many times the limit of a search the
            reranker rescores.
        retriever: Builds the retrieval of a search over *storage*.
    """
    return GatedMemory(
        memory=IndexMemory[UUID, Chunk](
            storage=storage,
            retriever=lambda limit: [
                retriever(
                    storage=storage,
                    search=search,
                    query_embedder=query_embedder,
                    limit=limit * candidate_multiplier if reranker else limit,
                ),
                *([reranker] if reranker else []),
            ],
            select=lambda keys: storage.get_where(Chunk.id.in_(list(keys))),
        ),
        policy=EffectPolicy(write="deny"),
        name="chunks",
    )


async def document_memory(
    reach: Reach, *, search_type: SearchType = "hybrid"
) -> GatedMemory[UUID, Chunk]:
    """The :func:`chunk_memory` of the documents *reach* admits.

    Scoping is enforced entirely as a pre-filter at the SQL level: one
    subquery on ``documents`` (:func:`db_documents.accessible_chunks`)
    narrows the indexer, so the HNSW/FTS scan never visits a row the caller
    cannot see, neither one owned by another store nor one the
    conversation's document scope excludes.
    """
    index = await _INDEX.get()

    return chunk_memory(
        index.indexer.narrow(db_documents.accessible_chunks(reach)),
        search=SEARCHES[search_type],
        query_embedder=index.embed_query,
        reranker=index.reranker,
        candidate_multiplier=(
            1 if settings.rerank is None else settings.rerank.candidate_multiplier
        ),
    )


@dataclass(slots=True, frozen=True)
class _Source:
    """The document a chunk belongs to, as a search result shows it."""

    stem_path: str
    original_suffix: str | None
    store_key: str


async def _load_sources(documents: Collection[UUID]) -> dict[UUID, _Source]:
    """The documents of the chunks a search found, by id."""
    async with session() as s:
        rows = await s.execute(
            sa.select(
                Document.id,
                Document.stem_path,
                Document.original_suffix,
                Document.workspace_key,
            ).where(Document.id.in_(list(documents)))
        )

    return {
        key: _Source(stem_path, original_suffix, store_key)
        for key, stem_path, original_suffix, store_key in rows
    }


def build_search_tool(reach: Reach) -> ToolFunc:
    """Build a search tool over the :func:`document_memory` of *reach*."""
    store_index = {s.store_key: s for s in reach.stores}

    async def memory(search_type: SearchType) -> GatedMemory[UUID, Chunk]:
        return await document_memory(reach, search_type=search_type)

    async def enrich(hits: Sequence[Hit[UUID, Chunk]]) -> list[RetrievedChunk]:
        if not hits:
            return []

        sources = await _load_sources({hit.value.document_id for hit in hits})
        results: list[RetrievedChunk] = []

        for hit in hits:
            chunk = hit.value
            source = sources.get(chunk.document_id)
            store = None if source is None else store_index.get(source.store_key)

            if source is None or store is None:
                continue

            results.append(
                RetrievedChunk(
                    store_key=source.store_key,
                    filename=store.scope.render(
                        Part(source.stem_path, "description").path
                    ),
                    chunk_index=chunk.idx,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    start_index=chunk.start_index,
                    end_index=chunk.end_index,
                    image_path=(
                        Part(source.stem_path, "original", source.original_suffix).path
                        if source.original_suffix is not None
                        and is_image_suffix(source.original_suffix)
                        else None
                    ),
                )
            )

        return results

    return search(memory=memory, result_mapper=enrich)
