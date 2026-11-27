"""Tests for context-window overflow classification of what pydantic-ai misses itself."""

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


@pytest.mark.parametrize(
    "error",
    [
        ModelHTTPError.for_category(
            ModelContextWindowExceededError, status_code=400, model_name="gpt-5.2"
        ),
        # llama.cpp names the overflow in a structured type.
        ModelHTTPError.for_category(
            None,
            status_code=400,
            model_name="qwen3.8-27b",
            body={"code": 400, "type": "exceed_context_size_error"},
            provider_error_type="exceed_context_size_error",
        ),
        # SGLang's body is flat with a generic type, so only its message names the overflow.
        ModelHTTPError(
            status_code=400,
            model_name="qwen3.5-27b",
            body={
                "message": "The input (5000 tokens) is longer than the model's context length (4096 tokens).",
                "type": "BadRequest",
                "code": 400,
            },
        ),
    ],
    ids=["pydantic-ai", "llama.cpp", "sglang"],
)
def test_an_overflow_is_told_from_the_error(error: ModelHTTPError) -> None:
    assert is_context_overflow(error, [])


@pytest.mark.parametrize(
    "error",
    [
        ValueError("boom"),
        ModelHTTPError(status_code=400, model_name="m", body={"message": "bad param"}),
        UnexpectedModelBehavior("invalid tool call"),
    ],
)
def test_unrelated_errors_pass_through(error: Exception) -> None:
    assert not is_context_overflow(error, [])


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
