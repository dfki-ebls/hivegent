"""Tests for the cross-cutting run-loop safeguards."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import IncompleteToolCall
from pydantic_ai.messages import (
    FinishReason,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from hivegent.agents.app import base_agent
from hivegent.agents.guards import (
    IncompleteToolCallGuard,
    IterationLimitWarner,
    PromptImageLimit,
    _images,
)

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


def _image() -> BinaryContent:
    return BinaryContent(data=b"\x89PNG", media_type="image/png")


def _read(tool_call_id: str) -> ToolReturnPart:
    """A binary read's return, shaped as pydantic-ai transcribes one."""
    return ToolReturnPart(
        tool_name="read_binary_document",
        content=["here it is", _image()],
        tool_call_id=tool_call_id,
    )


async def _sent(
    max_images: int | None, messages: list[ModelMessage]
) -> list[ModelMessage]:
    """The messages the guard puts on the wire for a request carrying *messages*."""
    sent: list[ModelMessage] = []

    async def handler(request_context: ModelRequestContext) -> ModelResponse:
        sent.extend(request_context.messages)

        return ModelResponse(parts=[TextPart(content="ok")])

    await PromptImageLimit(max_images=max_images).wrap_model_request(
        cast(Any, None),
        request_context=ModelRequestContext(
            model=cast(Any, None),
            messages=messages,
            model_settings=None,
            model_request_parameters=cast(Any, None),
        ),
        handler=handler,
    )

    return sent


async def test_the_whole_request_shares_one_image_allowance() -> None:
    """What the gateway counts is the request, so that is what is counted.

    The turn's own attachment and the two parallel reads of one step answer
    to a single number, which no per-call budget could hold them to, and the
    newest are the ones that survive.
    """
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content=["look at this", _image()])]),
        ModelRequest(parts=[_read("a"), _read("b")]),
    ]

    sent = await _sent(2, messages)

    assert len(_images(sent)) == 2
    prompt = sent[0].parts[0]
    assert isinstance(prompt, UserPromptPart)
    assert isinstance(prompt.content, list)
    assert prompt.content[0] == "look at this"
    assert "image not shown" in str(prompt.content[1])


async def test_the_trim_is_spent_on_the_wire_and_not_on_the_conversation() -> None:
    """The run's own messages are the tree the conversation is replayed from.

    Both halves matter: the parts are shared with it, so an in-place swap
    would strip the image from the stored conversation, and the list is its
    history, so handing back a rebuilt one would rewrite it wholesale.
    """
    part = _read("a")
    messages: list[ModelMessage] = [ModelRequest(parts=[part, _read("b")])]

    sent = await _sent(1, messages)

    assert len(_images(sent)) == 1
    assert len(_images(messages)) == 2
    assert isinstance(part.content, list)
    assert isinstance(part.content[1], BinaryContent)


@pytest.mark.parametrize("max_images", [None, 2])
async def test_a_request_the_gateway_accepts_is_passed_through(
    max_images: int | None,
) -> None:
    """No cap, or nothing over it, and the request is handed on untouched."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content=["look", _image(), _image()])])
    ]

    assert await _sent(max_images, messages) == messages


async def test_the_wrap_up_note_stays_off_the_conversation() -> None:
    """A note appended to the run's messages would be recorded as a user turn.

    It would then replay on every later turn and gain one more note per
    warned request, so the nudge has to ride the wire alone.
    """
    warner = IterationLimitWarner(max_requests=2)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="go")])]
    sent: list[ModelMessage] = []

    async def handler(request_context: ModelRequestContext) -> ModelResponse:
        sent.extend(request_context.messages)

        return ModelResponse(parts=[TextPart(content="ok")])

    await warner.wrap_model_request(
        cast(Any, SimpleNamespace(usage=SimpleNamespace(requests=2))),
        request_context=ModelRequestContext(
            model=cast(Any, None),
            messages=messages,
            model_settings=None,
            model_request_parameters=cast(Any, None),
        ),
        handler=handler,
    )

    assert "[run-limit-warning]" in str(sent[-1].parts[0])
    assert len(sent) == 2
    assert len(messages) == 1
