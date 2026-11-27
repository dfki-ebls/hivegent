"""The models created for OpenAI-compatible endpoints: their streaming and their profiles."""

from typing import Any, cast

import httpx2
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

from hivegent.config.llm import (
    OPENAI_BASE_URL,
    InferenceProvider,
    LlmProviderSettings,
    ModelFamily,
)
from hivegent.llm import create_openai_chat_model


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
        http_client=httpx2.AsyncClient(),
    )


def _choice(delta: ChoiceDelta) -> Choice:
    return Choice(delta=delta, index=0)


def test_content_after_tool_call_starts_a_new_text_part() -> None:
    """vLLM's parser returns to content mode after ``</tool_call>``, which must stay an ordered part stream."""
    model = _model("test-model", InferenceProvider.VLLM)
    response = model._streamed_response_cls(
        model_request_parameters=ModelRequestParameters(),
        _model_name=model.model_name,
        _model_profile=model.profile,
        _response=cast(Any, None),
        _provider_name="test",
        _model_id_namespace="test",
        _provider_url=OPENAI_BASE_URL,
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
    # The newline Qwen emits after ``</tool_call>`` is dropped, not a text part of its own.
    assert response._parts_manager.get_parts() == [
        TextPart(content="Before during"),
        ToolCallPart(
            tool_name="lookup", args='{"query":"value"}', tool_call_id="call-1"
        ),
        TextPart(content="After"),
    ]


def test_openai_endpoint_gets_no_self_hosted_quirks() -> None:
    """Thinking support tracks the model name, so the override is gone rather than agreeing by chance."""
    reasoning = _model("gpt-5", InferenceProvider.OPENAI)

    assert type(reasoning) is OpenAIChatModel
    assert reasoning.profile.get("supports_thinking") is True
    assert reasoning.profile.get("ignore_streamed_leading_whitespace") is False
    assert (
        _model("gpt-4o", InferenceProvider.OPENAI).profile.get("supports_thinking")
        is False
    )


def test_only_vllm_merges_leading_system_messages() -> None:
    """llama.cpp takes the shared self-hosted profile without the merge."""
    vllm = _model("some-local-model", InferenceProvider.VLLM)
    llama_cpp = _model("some-local-model", InferenceProvider.LLAMA_CPP)

    assert vllm.profile.get("openai_chat_supports_multiple_system_messages") is False
    assert (
        llama_cpp.profile.get("openai_chat_supports_multiple_system_messages") is None
    )

    for self_hosted in (vllm, llama_cpp):
        assert self_hosted.profile.get("supports_thinking") is True
        assert self_hosted.profile.get("ignore_streamed_leading_whitespace") is True

    # The whitespace belongs to the local chat template, not to a family that says otherwise.
    non_r1 = _model(
        "deepseek-ai/DeepSeek-V3", InferenceProvider.LLAMA_CPP, ModelFamily.DEEPSEEK
    )
    assert non_r1.profile.get("ignore_streamed_leading_whitespace") is True


def test_self_hosted_models_resolve_their_configured_family_profile() -> None:
    """The family wins over the generic profile, whose schema transformer a local runtime does not want."""
    qwen = _model("qwen3-32b", InferenceProvider.LLAMA_CPP, ModelFamily.QWEN)
    unknown = _model("qwen3-32b", InferenceProvider.LLAMA_CPP)

    assert (
        qwen.profile.get("json_schema_transformer") is InlineDefsJsonSchemaTransformer
    )
    transformer = unknown.profile.get("json_schema_transformer")
    assert transformer is not None
    assert issubclass(transformer, OpenAIJsonSchemaTransformer)


@pytest.mark.parametrize(
    "runtime",
    [runtime for runtime in InferenceProvider if runtime.writes_text],
)
def test_self_hosted_runtimes_are_never_named_openai(
    runtime: InferenceProvider,
) -> None:
    """An ``openai`` name would price a self-hosted model as OpenAI's namesake."""
    model = _model("some-local-model", runtime, context_window=4096)

    assert model.system == runtime.value
    assert model.context_window == 4096
