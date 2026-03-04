"""Response quality evaluation tests."""

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from hivegent.agent import UserDeps, explore_toolset, rag_toolset, user_agent
from hivegent.chunks import chunk_document
from hivegent.retrieval import sync_index
from hivegent.store import Casebase

pytestmark = pytest.mark.slow


async def _seed_and_get_deps(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> UserDeps:
    """Seed documents and return UserDeps."""
    docs_dir = user_store.workspace_dir(data_dir)

    for ann in annotations:
        for doc_name in ann["relevant_documents"]:
            doc_path = docs_dir / doc_name
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            content = f"# {doc_name}\n\n{ann['question']}\n\n{ann['expected_answer']}\n"
            doc_path.write_text(content, encoding="utf-8")
            await chunk_document(user_store, doc_name, content)

    sync_index(user_store)
    return UserDeps(user_id="testuser", store=user_store)


async def test_response_with_test_model(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """TestModel agent produces a string response for each annotation query."""
    deps = await _seed_and_get_deps(data_dir, user_store, annotations)

    for ann in annotations:
        # Use explore_toolset to avoid explore_documents calling a real LLM.
        result = await user_agent.run(
            ann["question"],
            model=TestModel(custom_output_text=ann["expected_answer"]),
            deps=deps,
            toolsets=[explore_toolset],
        )
        assert isinstance(result.output, str)
        assert len(result.output) > 0


@pytest.mark.skipif(
    not os.environ.get("HIVEGENT_LLM__API_KEY"),
    reason="HIVEGENT_LLM__API_KEY not set",
)
async def test_response_contains_expected_text(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """Real LLM response contains keywords from the expected answer."""
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from hivegent.config import settings

    deps = await _seed_and_get_deps(data_dir, user_store, annotations)

    model = OpenAIResponsesModel(
        settings.llm.model,
        provider=OpenAIProvider(
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url or None,
        ),
    )

    for ann in annotations:
        result = await user_agent.run(
            ann["question"],
            model=model,
            deps=deps,
            toolsets=[rag_toolset],
        )
        output_lower = result.output.lower()
        # Check that at least some key words from expected answer appear
        expected_words = [
            w.lower()
            for w in ann["expected_answer"].split()
            if len(w) > 4  # skip short words
        ]
        matches = sum(1 for w in expected_words if w in output_lower)
        assert matches > 0, (
            f"Response for {ann['question']!r} did not contain any "
            f"expected keywords. Output: {result.output[:200]}"
        )
