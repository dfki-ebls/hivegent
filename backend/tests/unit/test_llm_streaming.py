"""Regression tests for OpenAI-compatible streaming behavior."""

from typing import Any, cast

import pytest
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from pydantic import SecretStr
from pydantic_ai.messages import PartStartEvent, TextPart, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer

import hivegent.llm as llm_module
from hivegent.config.llm import (
    OPENAI_BASE_URL,
    InferenceProvider,
    LlmProviderSettings,
    ModelFamily,
)
from hivegent.llm import create_openai_chat_model
from hivegent.security import OperatorUrl


def _choice(delta: ChoiceDelta) -> Choice:
    return Choice(delta=delta, index=0)


def test_content_after_tool_call_starts_a_new_text_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interleaved vLLM content must remain a valid ordered part stream.

    vLLM's ``qwen3_xml`` parser returns to content mode after ``</tool_call>``,
    which pydantic-ai answers by starting a new text part.
    """
    monkeypatch.setattr(llm_module, "get_trusted_http_client", lambda *_: None)
    model = create_openai_chat_model(
        "test-model",
        LlmProviderSettings(
            base_url=OperatorUrl("http://localhost/v1"),
            api_key=SecretStr("test"),
            inference_provider=InferenceProvider.VLLM,
        ),
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


def _model(
    model: str,
    provider: InferenceProvider,
    family: ModelFamily | None = None,
    context_window: int | None = None,
) -> OpenAIChatModel:
    return create_openai_chat_model(
        model,
        LlmProviderSettings(
            base_url=OPENAI_BASE_URL,
            api_key=SecretStr("test"),
            inference_provider=provider,
        ),
        family=family,
        context_window=context_window,
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
    monkeypatch.setattr(llm_module, "get_trusted_http_client", lambda *_: None)

    reasoning = _openai_model("gpt-5")

    assert type(reasoning) is OpenAIChatModel
    assert reasoning.profile.get("supports_thinking") is True
    assert reasoning.profile.get("ignore_streamed_leading_whitespace") is False
    assert _openai_model("gpt-4o").profile.get("supports_thinking") is False


def test_only_vllm_merges_leading_system_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The system-message merge answers vLLM alone.

    llama.cpp accepts several system messages, so it takes the shared
    self-hosted profile without the merge.  The
    shared half is asserted on a name no family claims, so it witnesses our own
    layer rather than a family that happens to agree with it.
    """
    monkeypatch.setattr(llm_module, "get_trusted_http_client", lambda *_: None)

    vllm = _model("some-local-model", InferenceProvider.VLLM)
    llama_cpp = _model("some-local-model", InferenceProvider.LLAMA_CPP)

    assert vllm.profile.get("openai_chat_supports_multiple_system_messages") is False
    assert (
        llama_cpp.profile.get("openai_chat_supports_multiple_system_messages") is None
    )

    for self_hosted in (vllm, llama_cpp):
        assert self_hosted.profile.get("supports_thinking") is True
        assert self_hosted.profile.get("ignore_streamed_leading_whitespace") is True

    # ``deepseek_model_profile`` states the whitespace flag as ``False`` for
    # anything but R1, and our layer sits above the family for exactly this:
    # the whitespace belongs to the local chat template, not to the model.
    non_r1 = _model(
        "deepseek-ai/DeepSeek-V3", InferenceProvider.LLAMA_CPP, ModelFamily.DEEPSEEK
    )
    assert non_r1.profile.get("ignore_streamed_leading_whitespace") is True


def test_self_hosted_models_resolve_their_configured_family_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured family picks the profile, not the generic one.

    The schema transformer is the sharpest witness: the generic profile keeps
    the ``$ref``/``$defs`` a local runtime wants inlined, so seeing the Qwen
    one is proof the family won, and a bare Qwen name without a family keeps
    it.
    """
    monkeypatch.setattr(llm_module, "get_trusted_http_client", lambda *_: None)

    qwen = _model("qwen3-32b", InferenceProvider.LLAMA_CPP, ModelFamily.QWEN)
    unknown = _model("qwen3-32b", InferenceProvider.LLAMA_CPP)

    assert (
        qwen.profile.get("json_schema_transformer") is InlineDefsJsonSchemaTransformer
    )
    transformer = unknown.profile.get("json_schema_transformer")
    assert transformer is not None
    assert issubclass(transformer, OpenAIJsonSchemaTransformer)


def test_self_hosted_runtimes_are_never_named_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each runtime's provider is named by its value, OpenAI's API alone ``openai``.

    An ``openai`` name would price a self-hosted model as OpenAI's namesake and
    count its embedding tokens with OpenAI's tokenizer.
    """
    monkeypatch.setattr(llm_module, "get_trusted_http_client", lambda *_: None)

    for runtime in InferenceProvider:
        model = _model("some-local-model", runtime, context_window=4096)

        assert model.system == runtime.value
        assert model.context_window == 4096
