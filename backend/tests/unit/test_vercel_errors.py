"""Tests for canonical chat-stream error codes.

An HTTP rejection is a provider's own contract, so those cases carry a
captured body.  The two overflows that surface as an exception instead are
raised by pydantic-ai, which spells one of them as prose in a generic
``UnexpectedModelBehavior`` — so those are driven through a real run
(:func:`_run_error`) rather than restated here.  A hand-written copy of that
sentence would keep passing after an upstream reword, while the compaction
offer silently stopped appearing.
"""

import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ModelResponsePart,
    ThinkingPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents.app import base_agent
from hivegent.server.vercel import CONTEXT_LENGTH_EXCEEDED, chat_error_text


def _is_overflow(error: Exception) -> bool:
    return chat_error_text(error).startswith(f"{CONTEXT_LENGTH_EXCEEDED}: ")


async def _run_error(parts: list[ModelResponsePart]) -> Exception:
    """The error a run raises when the model returns *parts* cut off by the limit."""

    def truncated(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=parts, finish_reason="length")

    with pytest.raises(UnexpectedModelBehavior) as raised:
        await base_agent.run("go", model=FunctionModel(truncated))

    return raised.value


def test_openai_structured_code() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="gpt-5.2",
        body={"code": "context_length_exceeded", "message": "too long"},
    )
    assert _is_overflow(error)


def test_llamacpp_structured_type() -> None:
    error = ModelHTTPError(
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
    )
    assert _is_overflow(error)


def test_vllm_prose_body() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="gemma-4",
        body={
            "object": "error",
            "message": "This model's maximum context length is 8192 tokens.",
            "type": "BadRequestError",
        },
    )
    assert _is_overflow(error)


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
    assert _is_overflow(error)


@pytest.mark.parametrize(
    "parts",
    [
        # Nothing generated at all, and the reasoning-only variant of it: the
        # limit went entirely on thinking.
        [],
        [ThinkingPart(content="hmm")],
        # Cut off mid tool call, which `IncompleteToolCallGuard` turns into an
        # error before the half-written call is dispatched.
        [ToolCallPart(tool_name="edit_document", args='{"file_path":')],
    ],
)
async def test_a_completion_that_ran_out_of_room_is_an_overflow(
    parts: list[ModelResponsePart],
) -> None:
    assert _is_overflow(await _run_error(parts))


def test_unrelated_errors_pass_through() -> None:
    assert chat_error_text(ValueError("boom")) == "boom"
    assert not _is_overflow(
        ModelHTTPError(status_code=400, model_name="m", body={"message": "bad param"})
    )
    assert not _is_overflow(UnexpectedModelBehavior("invalid tool call"))
