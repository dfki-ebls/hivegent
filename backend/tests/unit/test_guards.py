"""Tests for the cross-cutting run-loop safeguards."""

from typing import Any, cast

import pytest
from pydantic_ai.exceptions import IncompleteToolCall
from pydantic_ai.messages import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from hivegent.agents.app import base_agent
from hivegent.agents.guards import IncompleteToolCallGuard

_TRUNCATED_CALL = ToolCallPart(
    tool_name="edit_document", args='{"file_path":', tool_call_id="call-1"
)


async def _check(
    parts: list[ModelResponsePart],
    finish_reason: FinishReason,
    settings: ModelSettings | None = None,
) -> None:
    """Run the guard over a response with *parts*, as the agent loop would."""
    await IncompleteToolCallGuard().after_model_request(
        cast(Any, None),
        request_context=cast(
            Any,
            ModelRequestContext(
                model=cast(Any, None),
                messages=[],
                model_settings=settings,
                model_request_parameters=cast(Any, None),
            ),
        ),
        response=ModelResponse(parts=parts, finish_reason=finish_reason),
    )


async def test_tool_call_cut_off_by_the_token_limit_fails_the_turn() -> None:
    """Incomplete arguments must not reach the tool or the retry loop.

    Run through a real agent rather than the hook alone, so the guard riding
    on the agents themselves is what the assertion rests on: the tool named
    here is not even registered, and the turn still fails before validation
    would look for it.
    """

    def truncated(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[_TRUNCATED_CALL], finish_reason="length")

    with pytest.raises(IncompleteToolCall, match="provider default"):
        await base_agent.run("go", model=FunctionModel(truncated))


async def test_the_message_names_the_limit_that_was_hit() -> None:
    with pytest.raises(IncompleteToolCall, match="1024"):
        await _check([_TRUNCATED_CALL], "length", ModelSettings(max_tokens=1024))


@pytest.mark.parametrize(
    ("parts", "finish_reason"),
    [
        # A finished call: the guard reads the finish reason, not the arguments.
        ([_TRUNCATED_CALL], "stop"),
        # Truncated prose is a degraded but usable answer, so it passes.
        ([TextPart(content="half a sen")], "length"),
    ],
)
async def test_anything_else_passes_through(
    parts: list[ModelResponsePart], finish_reason: FinishReason
) -> None:
    await _check(parts, finish_reason)
