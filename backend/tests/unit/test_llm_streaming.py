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
from pydantic_ai.models.openai import OpenAIChatModel

import hivegent.llm as llm_module
from hivegent.config import InferenceProvider
from hivegent.llm import create_openai_chat_model


def _choice(delta: ChoiceDelta) -> Choice:
    return Choice(delta=delta, index=0)


def test_content_after_tool_call_starts_a_new_text_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interleaved vLLM content must remain a valid ordered part stream."""
    monkeypatch.setattr(llm_module, "get_http_client", lambda **_: None)
    model = create_openai_chat_model(
        "test-model",
        api_key="test",
        base_url="http://localhost/v1",
        inference_provider=InferenceProvider.VLLM,
    )
    response = model._streamed_response_cls(
        model_request_parameters=ModelRequestParameters(),
        _model_name=model.model_name,
        _model_profile=model.profile,
        _response=cast(Any, None),
        _provider_name="test",
        _model_id_namespace="test",
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
                        function=ChoiceDeltaToolCallFunction(arguments='{"query":'),
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


def _model(model: str, provider: InferenceProvider) -> OpenAIChatModel:
    return create_openai_chat_model(
        model, api_key="test", base_url=None, inference_provider=provider
    )


def _openai_model(model: str) -> OpenAIChatModel:
    return _model(model, InferenceProvider.OPENAI)


def test_openai_endpoint_gets_no_self_hosted_quirks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec-compliant API keeps pydantic-ai's own model and profile.

    Thinking support is the sharpest witness: forcing it on would report a
    non-reasoning model as reasoning, so seeing it track the model name is
    proof the override is gone rather than merely agreeing by chance.
    """
    monkeypatch.setattr(llm_module, "get_http_client", lambda **_: None)

    reasoning = _openai_model("gpt-5")

    assert type(reasoning) is OpenAIChatModel
    assert reasoning.profile.get("supports_thinking") is True
    assert reasoning.profile.get("ignore_streamed_leading_whitespace") is False
    assert _openai_model("gpt-4o").profile.get("supports_thinking") is False


def test_only_vllm_gets_the_qwen3_xml_parser_workarounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Segmentation and the system-message merge answer vLLM's parser alone.

    llama.cpp orders messages correctly and accepts several system messages,
    so it takes the shared self-hosted profile without either workaround.
    """
    monkeypatch.setattr(llm_module, "get_http_client", lambda **_: None)

    vllm = _model("qwen", InferenceProvider.VLLM)
    llama_cpp = _model("qwen", InferenceProvider.LLAMA_CPP)

    assert type(vllm) is llm_module._SegmentedOpenAIChatModel
    assert type(llama_cpp) is OpenAIChatModel
    assert vllm.profile.get("openai_chat_supports_multiple_system_messages") is False
    assert (
        llama_cpp.profile.get("openai_chat_supports_multiple_system_messages") is None
    )

    for self_hosted in (vllm, llama_cpp):
        assert self_hosted.profile.get("supports_thinking") is True
        assert self_hosted.profile.get("ignore_streamed_leading_whitespace") is True
