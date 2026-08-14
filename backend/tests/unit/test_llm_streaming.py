"""Regression tests for OpenAI-compatible streaming behavior."""

from typing import Any, cast

import pytest
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from pydantic_ai.messages import PartStartEvent, TextPart, ToolCallPart
from pydantic_ai.models import ModelRequestParameters

import hivegent.llm as llm_module
from hivegent.llm import create_openai_chat_model


def _choice(delta: ChoiceDelta) -> Choice:
    return Choice(delta=delta, index=0)


def test_content_after_tool_call_starts_a_new_text_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interleaved vLLM content must remain a valid ordered part stream."""
    monkeypatch.setattr(llm_module, "get_http_client", lambda **_: None)
    model = create_openai_chat_model(
        "test-model", api_key="test", base_url="http://localhost/v1"
    )
    response = model._streamed_response_cls(
        model_request_parameters=ModelRequestParameters(),
        _model_name=model.model_name,
        _model_profile=model.profile,
        _response=cast(Any, None),
        _provider_name="test",
        _provider_url="http://localhost/v1",
    )
    choices = [
        _choice(ChoiceDelta(content="Before")),
        _choice(
            ChoiceDelta(
                tool_calls=[
                    ChoiceDeltaToolCall(
                        index=0,
                        id="call-1",
                        type="function",
                        function=ChoiceDeltaToolCallFunction(
                            arguments='{"query":'
                        ),
                    )
                ]
            )
        ),
        _choice(ChoiceDelta(content=" during")),
        _choice(
            ChoiceDelta(
                tool_calls=[
                    ChoiceDeltaToolCall(
                        index=0,
                        function=ChoiceDeltaToolCallFunction(
                            name="lookup", arguments='"value"}'
                        ),
                    )
                ]
            )
        ),
        _choice(ChoiceDelta(content="\n")),
        _choice(ChoiceDelta(content="After")),
    ]

    events = [event for choice in choices for event in response._map_part_delta(choice)]
    starts = [event for event in events if isinstance(event, PartStartEvent)]

    assert [(event.index, type(event.part)) for event in starts] == [
        (0, TextPart),
        (1, ToolCallPart),
        (2, TextPart),
    ]
    # The resumed segment starts on the profile's ignored leading whitespace,
    # so the newline Qwen emits after ``</tool_call>`` is dropped rather than
    # rendered as a text part of its own.
    assert response._parts_manager.get_parts() == [
        TextPart(content="Before during"),
        ToolCallPart(
            tool_name="lookup", args='{"query":"value"}', tool_call_id="call-1"
        ),
        TextPart(content="After"),
    ]
