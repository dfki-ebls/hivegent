"""Tests for context-window overflow classification.

pydantic-ai files OpenAI's code and vLLM's wording itself, so only what it
misses is pinned here: llama.cpp's error type and SGLang's prose, with captured
bodies, and the empty response cut for length that `exar.pydantic_ai.compaction.is_context_overflow`
tells from one that spent its cap thinking, driven through a real run (:func:`_run_error`)
rather than restated, so an upstream change fails here instead of silently
ending compaction.
"""

import pytest
from pydantic_ai import capture_run_messages
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
from pydantic_ai.usage import RequestUsage

from hivegent.agents.app import title_agent
from hivegent.agents.compaction import is_overflow as is_context_overflow


async def _run_error(
    parts: list[ModelResponsePart],
) -> tuple[Exception, list[ModelMessage]]:
    """The error a run raises when the model returns *parts* cut off by the limit, with its messages."""

    def truncated(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Reported usage, since `FunctionModel` estimates output tokens otherwise.
        return ModelResponse(
            parts=parts, finish_reason="length", usage=RequestUsage(input_tokens=1)
        )

    with (
        capture_run_messages() as sent,
        pytest.raises(UnexpectedModelBehavior) as raised,
    ):
        await title_agent.run("go", model=FunctionModel(truncated))

    return raised.value, sent


def test_pydantic_ai_category() -> None:
    error = ModelHTTPError.for_category(
        ModelContextWindowExceededError, status_code=400, model_name="gpt-5.2"
    )
    assert is_context_overflow(error, [])


def test_llamacpp_structured_type() -> None:
    error = ModelHTTPError.for_category(
        None,
        status_code=400,
        model_name="qwen3.8-27b",
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
    assert is_context_overflow(error, [])


def test_sglang_prose_body() -> None:
    # SGLang's body is flat (no nested `error` object), with a numeric
    # code and a generic type, only the message names the overflow.
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
    assert is_context_overflow(error, [])


@pytest.mark.parametrize(
    ("parts", "no_room"),
    [
        # Nothing generated at all is a window without room for output, while
        # a limit that went entirely on thinking is a cap the reply spent.
        ([], True),
        ([ThinkingPart(content="hmm")], False),
    ],
)
async def test_only_a_completion_without_room_is_an_overflow(
    parts: list[ModelResponsePart], no_room: bool
) -> None:
    error, sent = await _run_error(parts)

    assert is_context_overflow(error, sent) is no_room


def test_unrelated_errors_pass_through() -> None:
    assert not is_context_overflow(ValueError("boom"), [])
    assert not is_context_overflow(
        ModelHTTPError(status_code=400, model_name="m", body={"message": "bad param"}),
        [],
    )
    assert not is_context_overflow(UnexpectedModelBehavior("invalid tool call"), [])
