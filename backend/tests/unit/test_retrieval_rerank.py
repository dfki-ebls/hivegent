"""Unit tests for reranker config gating and the search over the document memory."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import httpx2
import pytest
from cbrkit.indexable import Hybrid, Pgvector, Unscoped
from cbrkit.retrieve.rerank import HTTP, CrossEncoder, Rerank
from cbrkit.typing import Casebase, SimMap
from exar.memory import InMemoryMemory
from exar.text import LineClip
from exar.typing import Hit
from pydantic import SecretStr, ValidationError
from pydantic_ai import Tool, models
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy.ext.asyncio import create_async_engine

from hivegent import retrieval
from hivegent.config import settings
from hivegent.config.llm import LlmProviderSettings
from hivegent.config.retrieval import (
    LocalEmbeddingSettings,
    LocalRerankSettings,
    RemoteEmbeddingSettings,
    RemoteRerankSettings,
)
from hivegent.db.models import Chunk
from hivegent.security import OperatorUrl
from hivegent.tools.retrieval import (
    RetrievedChunk,
    SearchType,
    _format_results,
    search,
)

type _Batches = Sequence[tuple[Casebase[UUID, str], str]]


def _chunk(key: UUID, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        filename=str(key),
        chunk_index=0,
        text=text,
        token_count=1,
        start_line=1,
        end_line=1,
        start_index=0,
        end_index=len(text),
    )


async def _chunks(hits: Sequence[Hit[UUID, Chunk]]) -> list[RetrievedChunk]:
    return [_chunk(hit.key, hit.value.text) for hit in hits]


def _row(text: str) -> Chunk:
    return Chunk(
        text=text,
        document_id=UUID(int=0),
        idx=0,
        token_count=1,
        start_index=0,
        end_index=len(text),
        start_line=1,
        end_line=1,
    )


def _storage() -> Pgvector[UUID, Chunk]:
    """A chunk storage that never connects, for a stubbed retriever."""
    return Pgvector[UUID, Chunk](
        engine=create_async_engine("postgresql+psycopg://"),
        model=Chunk,
        index_type="sparse",
        value_column=Chunk.text.key,
        tsvector_column=Chunk.tsv.key,
        scope=Unscoped.TABLE,
    )


async def _empty(_search_type: SearchType) -> InMemoryMemory[UUID, Chunk]:
    return InMemoryMemory()


type _Rankings = list[tuple[Casebase[UUID, str], SimMap[UUID, float]]]


def test_search_query_minimum_is_published_and_validated() -> None:
    schema = Tool(search(memory=_empty, result_mapper=_chunks)).function_schema
    queries = schema.json_schema["properties"]["queries"]

    assert queries["items"]["minLength"] == 20
    schema.validator.validate_python({"queries": ["x" * 20]})

    with pytest.raises(ValidationError, match="at least 20 characters"):
        schema.validator.validate_python({"queries": ["x" * 20, "x" * 19]})


def test_search_publishes_the_chunk_schema() -> None:
    schema = Tool(
        search(memory=_empty, result_mapper=_chunks)
    ).function_schema.return_schema

    assert schema["items"]["$ref"].endswith("/RetrievedChunk")


def test_format_results_truncates_long_lines() -> None:
    # A long line in a chunk (e.g. a base64 image) must be clipped so the
    # formatted block cannot flood the model context.
    formatted = _format_results([_chunk(UUID(int=0), "A" * 5_000)], LineClip(80))
    assert "…" in formatted
    assert len(max(formatted.splitlines(), key=len)) < 200


def test_build_reranker_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rerank", None)
    assert retrieval._reranker() is None


def test_build_reranker_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "rerank",
        LocalRerankSettings(provider="sentence-transformers", model="cross-x"),
    )
    assert retrieval._reranker() == Rerank(
        model=CrossEncoder(model="cross-x", device="cpu")
    )


def test_build_reranker_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "rerank",
        RemoteRerankSettings(
            provider="http", endpoint="local", model="rerank-x", top_n=3
        ),
    )
    monkeypatch.setitem(
        settings.llm.providers,
        "local",
        LlmProviderSettings(
            base_url=OperatorUrl("https://reranker.test/v1"),
            api_key=SecretStr("secret"),
        ),
    )
    client = httpx2.AsyncClient()
    monkeypatch.setattr(retrieval, "get_trusted_http_client", lambda *_: client)
    reranker = retrieval._reranker()

    assert reranker == Rerank(
        model=HTTP(
            model="rerank-x",
            url="https://reranker.test/v1/rerank",
            client=client,
            api_key="secret",
        ),
        limit=3,
    )


async def test_search_batches_the_queries_and_fuses_the_reranked_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b, c, d, e = (UUID(int=i) for i in range(5))
    ranks = {"q1": [a, b, c], "q2": [d, e, c]}
    rankings = {q: {k: 1 / (i + 1) for i, k in enumerate(r)} for q, r in ranks.items()}
    captured: dict[str, Any] = {"retrieved": [], "reranked": [], "reads": 0}

    async def retrieve(batches: _Batches, /) -> _Rankings:
        captured["retrieved"].append([q for _, q in batches])

        return [({k: str(k) for k in rankings[q]}, rankings[q]) for _, q in batches]

    async def rerank(batches: _Batches, /) -> _Rankings:
        captured["reranked"].extend(q for _, q in batches)

        return [(cases, rankings[q]) for cases, q in batches]

    def retriever(
        *, storage: object, search: object, query_embedder: object, limit: int
    ):
        captured.update(search=search, limit=limit)

        return retrieve

    async def rows(_storage: object, _where: object) -> dict[UUID, Chunk]:
        captured["reads"] += 1

        return {key: _row(str(key)) for key in (a, b, c, d, e)}

    monkeypatch.setattr(retrieval, "PgvectorRetriever", retriever)
    monkeypatch.setattr(Pgvector, "get_where", rows)

    async def memory(_search_type: SearchType):
        return retrieval.chunk_memory(
            _storage(), search=Hybrid(), reranker=rerank, candidate_multiplier=4
        )

    tool = search(memory=memory, result_mapper=_chunks)
    output = await tool(["q1", "q2", "q1"], max_results=1)

    # One batch over-fetches 1 * 4 candidates of each query, reranked once.
    assert captured == {
        "search": Hybrid(),
        "limit": 4,
        "retrieved": [["q1", "q2"]],
        "reranked": ["q1", "q2"],
        "reads": 1,
    }
    # c ranks third for both queries, which fusing the whole pools finds.
    assert [r.text for r in output.metadata] == [str(c)]


async def test_warm_index_loads_the_local_embedder_and_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def embed(values: Sequence[str], /) -> list[Any]:
        calls.append("embed")

        return [None for _ in values]

    async def score(
        query: str, documents: Sequence[str], /, limit: int | None = None
    ) -> dict[int, float]:
        calls.append("rerank")

        return dict.fromkeys(range(len(documents)), 0.0)

    index = retrieval._Index(
        embed=embed,
        embed_query=embed,
        indexer=_storage(),
        reranker=Rerank(model=score),
    )
    monkeypatch.setattr(retrieval, "_INDEX", retrieval._AsyncLazy(lambda: index))
    monkeypatch.setattr(settings, "embedding", LocalEmbeddingSettings())
    monkeypatch.setattr(
        settings,
        "rerank",
        LocalRerankSettings(provider="sentence-transformers", model="cross-x"),
    )
    await retrieval.warm_index()

    assert calls == ["embed", "rerank"]


async def test_openai_embedders_send_the_prefixes_and_request_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        data = [
            {"object": "embedding", "index": i, "embedding": [0.0]}
            for i in range(len(body["input"]))
        ]

        return httpx2.Response(
            200, json={"object": "list", "data": data, "model": body["model"]}
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    monkeypatch.setattr(
        retrieval,
        "create_openai_provider",
        lambda _: OpenAIProvider(
            base_url="https://embed.test/v1", api_key="x", http_client=client
        ),
    )
    monkeypatch.setitem(
        settings.llm.providers,
        "local",
        LlmProviderSettings(base_url=OperatorUrl("https://embed.test/v1")),
    )
    monkeypatch.setattr(
        settings,
        "embedding",
        RemoteEmbeddingSettings(
            provider="openai",
            endpoint="local",
            model="e5",
            dimension=1,
            query_prefix="query: ",
            document_prefix="passage: ",
            request={"extra_body": {"truncate_prompt_tokens": -1}},
        ),
    )
    documents, queries = retrieval._embedders()
    _ = await documents(["a"])
    _ = await queries(["b"])

    assert [(b["input"], b["truncate_prompt_tokens"]) for b in bodies] == [
        (["passage: a"], -1),
        (["query: b"], -1),
    ]
