"""Unit tests for reranker config gating and search over-fetch wiring."""

from typing import Any, cast

import pytest
from hivegent import retrieval
from hivegent.config import settings
from hivegent.tools.retrieval import VectorSearchTool


def test_build_reranker_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.rerank, "provider", None)
    assert retrieval._build_reranker() is None


def test_build_reranker_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(settings.rerank, "provider", "sentence-transformers")
    monkeypatch.setattr(settings.rerank, "model", "cross-x")
    monkeypatch.setattr(
        retrieval.cbrkit.retrieval.rerank,
        "cross_encoder",
        lambda **kw: seen.update(kw) or "CE",
    )
    assert retrieval._build_reranker() == "CE"
    assert seen == {"model": "cross-x"}


def test_build_reranker_http(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(settings.rerank, "provider", "http")
    monkeypatch.setattr(settings.rerank, "model", "rerank-x")
    monkeypatch.setattr(settings.rerank, "base_url", "https://reranker.test/v1")
    monkeypatch.setattr(settings.rerank, "api_key", "secret")
    monkeypatch.setattr(settings.rerank, "top_n", 3)
    monkeypatch.setattr(retrieval, "get_http_client", lambda **_: "CLIENT")
    monkeypatch.setattr(
        retrieval.cbrkit.retrieval.rerank,
        "http",
        lambda **kw: seen.update(kw) or "HTTP",
    )
    assert retrieval._build_reranker() == "HTTP"
    assert seen == {
        "model": "rerank-x",
        "url": "https://reranker.test/v1/rerank",
        "client": "CLIENT",
        "api_key": "secret",
        "top_n": 3,
    }


async def test_search_overfetches_and_appends_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hivegent.tools.retrieval as tr

    captured: dict[str, object] = {}

    class _Storage:
        async def has_index(self) -> bool:
            return True

    class _Step:
        ranking = ["k1"]
        similarities = {"k1": 0.9}
        casebase = {"k1": "candidate text"}

    class _Result:
        final_step = type("F", (), {"queries": {"default": _Step()}})()

    def fake_pgvector_async(*, storage, search_type, where, limit):
        captured["limit"] = limit
        return "BASE"

    async def fake_apply(query, retrievers):
        captured["retrievers"] = retrievers
        return _Result()

    monkeypatch.setattr(
        tr.cbrkit.retrieval.indexable, "pgvector_async", fake_pgvector_async
    )
    monkeypatch.setattr(tr.cbrkit.retrieval, "apply_query_indexed_async", fake_apply)

    async def storage_factory():
        return _Storage()

    async def reranker_factory():
        return "RERANKER"

    tool = VectorSearchTool(
        storage_factory=cast(Any, storage_factory),
        reranker_factory=cast(Any, reranker_factory),
        candidate_multiplier=4,
    )
    await tool("q", max_results=5)

    # Over-fetch 5 * 4 candidates and run the reranker as the second stage.
    assert captured["limit"] == 20
    assert captured["retrievers"] == ["BASE", "RERANKER"]
