"""Tests for context-window overflow classification.

pydantic-ai files OpenAI's code and vLLM's wording itself, so only what it
misses is pinned here: llama.cpp's error type and SGLang's prose, with captured
bodies, and the empty response pydantic-ai spells as prose in a generic
``UnexpectedModelBehavior``, driven through a real run (:func:`_run_error`)
rather than restated, so an upstream reword fails here instead of silently
ending compaction.
"""

import pytest
from pydantic_ai.exceptions import (
    ModelContextWindowExceededError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ModelResponsePart,
    ThinkingPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents.app import title_agent
from hivegent.llm import is_context_overflow


async def _run_error(parts: list[ModelResponsePart]) -> Exception:
    """The error a run raises when the model returns *parts* cut off by the limit."""

    def truncated(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=parts, finish_reason="length")

    with pytest.raises(UnexpectedModelBehavior) as raised:
        await title_agent.run("go", model=FunctionModel(truncated))

    return raised.value


def test_pydantic_ai_category() -> None:
    error = ModelHTTPError.for_category(
        ModelContextWindowExceededError, status_code=400, model_name="gpt-5.2"
    )
    assert is_context_overflow(error)


def test_llamacpp_structured_type() -> None:
    error = ModelHTTPError.for_category(
        None,
        status_code=400,
        model_name="qwen3.6-27b",
        body={
            "code": 400,
            "message": (
                "the request exceeds the available context size. try increasing "
                "the context size or enable context shift"
            ),
            "type": "exceed_context_size_error",
        },
        provider_error_type="exceed_context_size_error",
    )
    assert is_context_overflow(error)


def test_sglang_prose_body() -> None:
    # SGLang's body is flat (no nested `error` object), with a numeric
    # code and a generic type — only the message names the overflow.
    error = ModelHTTPError(
        status_code=400,
        model_name="qwen3.5-27b",
        body={
            "object": "error",
            "message": (
                "The input (5000 tokens) is longer than the model's "
                "context length (4096 tokens)."
            ),
            "type": "BadRequest",
            "param": None,
            "code": 400,
        },
    )
    assert is_context_overflow(error)


@pytest.mark.parametrize(
    "parts",
    [
        # Nothing generated at all, and the reasoning-only variant of it: the
        # limit went entirely on thinking.
        [],
        [ThinkingPart(content="hmm")],
    ],
)
async def test_a_completion_that_ran_out_of_room_is_an_overflow(
    parts: list[ModelResponsePart],
) -> None:
    assert is_context_overflow(await _run_error(parts))


def test_unrelated_errors_pass_through() -> None:
    assert not is_context_overflow(ValueError("boom"))
    assert not is_context_overflow(
        ModelHTTPError(status_code=400, model_name="m", body={"message": "bad param"})
    )
    assert not is_context_overflow(UnexpectedModelBehavior("invalid tool call"))
