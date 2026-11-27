"""LLM client construction helpers, including the quirk compensation that
self-hosted OpenAI-compatible endpoints need."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import httpx2
from exar.agents import call
from exar.pydantic_ai import PydanticAiAgent
from openai import AsyncOpenAI
from pydantic_ai import Agent, UserContent
from pydantic_ai.capabilities import (
    TRANSIENT_MODEL_ERRORS,
    AbstractCapability,
    RetryOnModelErrors,
)
from pydantic_ai.exceptions import ModelEmptyResponseError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import (
    InlineDefsJsonSchemaTransformer,
    ModelProfile,
    merge_profile,
)
from pydantic_ai.profiles.cohere import cohere_model_profile
from pydantic_ai.profiles.deepseek import deepseek_model_profile
from pydantic_ai.profiles.google import google_model_profile
from pydantic_ai.profiles.harmony import harmony_model_profile
from pydantic_ai.profiles.meta import meta_model_profile
from pydantic_ai.profiles.mistral import mistral_model_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.profiles.zai import zai_model_profile
from pydantic_ai.providers import Provider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
from pydantic_ai.providers.vllm import VLLMProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from .config import reveal, settings
from .config.llm import (
    FAMILY_PROVIDERS,
    InferenceProvider,
    LlmProviderSettings,
    ModelFamily,
)
from .http_client import get_trusted_http_client
from .llm_config import LlmConfig, clamp_reasoning

__all__ = [
    "MODEL_RETRIES",
    "SUMMARY_MAX_TOKENS",
    "StrictJsonSchemaTransformer",
    "complete",
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
    "model_from_config",
    "summary_model_settings",
    "thinking_model_settings",
]


def create_openai_client(
    provider: LlmProviderSettings, http_client: httpx2.AsyncClient | None = None
) -> AsyncOpenAI:
    """Create an OpenAI SDK client for *provider* on *http_client*, the shared one for its endpoint when ``None``."""
    return OpenAIProvider(
        api_key=reveal(provider.api_key),
        base_url=provider.base_url,
        http_client=http_client or get_trusted_http_client(provider.base_url),
    ).client


def create_openai_provider(
    provider: LlmProviderSettings, http_client: httpx2.AsyncClient | None = None
) -> Provider[AsyncOpenAI]:
    """The pydantic-ai provider of an operator endpoint, named for its runtime.

    Only OpenAI's own API takes pydantic-ai's OpenAI provider, which profiles,
    prices, and counts tokens as OpenAI does.  vLLM and Ollama take their own,
    and any other runtime an OpenAI-compatible one named after it, so no
    self-hosted model is mistaken for an OpenAI one of the same name.
    """
    client = create_openai_client(provider, http_client)
    runtime = provider.inference_provider

    match runtime:
        case InferenceProvider.OPENAI:
            return OpenAIProvider(openai_client=client)

        case InferenceProvider.VLLM:
            return VLLMProvider(openai_client=client)

        case InferenceProvider.OLLAMA:
            return OllamaProvider(openai_client=client)

        case (
            InferenceProvider.LLAMA_CPP
            | InferenceProvider.SGLANG
            | InferenceProvider.OPENAI_COMPATIBLE
        ):
            return OpenAICompatibleProvider(runtime.value, openai_client=client)


# The profile of each configured model family, the same functions pydantic-ai
# maps in ``VLLMProvider.model_profile``, which is not reusable here: it merges
# vLLM's own server quirks on top of them.
_FAMILY_PROFILES: Mapping[ModelFamily, Callable[[str], ModelProfile | None]] = {
    ModelFamily.LLAMA: meta_model_profile,
    ModelFamily.GEMMA: google_model_profile,
    ModelFamily.QWEN: qwen_model_profile,
    ModelFamily.DEEPSEEK: deepseek_model_profile,
    ModelFamily.MISTRAL: mistral_model_profile,
    ModelFamily.COHERE: cohere_model_profile,
    ModelFamily.GPT_OSS: harmony_model_profile,
    ModelFamily.GLM: zai_model_profile,
}

# The runtimes serving the operator's own models, rather than a hosted API.
_SELF_HOSTED = frozenset(
    {
        InferenceProvider.LLAMA_CPP,
        InferenceProvider.VLLM,
        InferenceProvider.SGLANG,
        InferenceProvider.OLLAMA,
    }
)

# What a self-hosted runtime needs on top of the provider and family profiles.
# ``supports_thinking`` because these endpoints serve reasoning models
# pydantic-ai cannot name-infer, so its ``False`` would silently strip the
# unified ``thinking`` setting from every request, and
# ``ignore_streamed_leading_whitespace`` because the whitespace is what the
# local chat templates emit around a tool call, whatever a family measured
# against its own hosted endpoint.  ``supports_inline_system_prompts`` is off
# because their chat templates reject a ``system`` message after the first user
# turn (see "Run state appends its changes" in the README).
_SELF_HOSTED_PROFILE = OpenAIModelProfile(
    supports_thinking=True,
    supports_inline_system_prompts=False,
    ignore_streamed_leading_whitespace=True,
)


class StrictJsonSchemaTransformer(InlineDefsJsonSchemaTransformer):
    """Inlines definitions and requires every property of a strict schema.

    vLLM enforces a strict schema with an xgrammar grammar that takes the
    properties in their declared order, so a model writing an optional
    property first could no longer write any declared before it, and a tool
    got only ``limit`` of ``path``, ``query`` and ``limit``.  Required, each
    property has its place, an argument typed ``X | None`` taking null and
    any other one its value, the default included.
    A nullable string made required takes no empty string, since a model
    forced to write it writes ``""`` for the null it means, which a tool such
    as ``delegate_task`` reads as a value (``resume=""`` naming no task).

    Examples:
        >>> schema = StrictJsonSchemaTransformer(
        ...     {
        ...         "type": "object",
        ...         "properties": {
        ...             "a": {"type": "string"},
        ...             "b": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        ...         },
        ...         "required": ["a"],
        ...     }
        ... ).walk()
        >>> schema["required"]
        ['a', 'b']
        >>> schema["properties"]["b"]["anyOf"][0]
        {'type': 'string', 'minLength': 1}
    """

    def transform(self, schema: dict[str, Any]) -> dict[str, Any]:
        """*schema* with every property required unless the schema is not strict."""
        if self.strict is not False and "properties" in schema:
            properties: dict[str, Any] = schema["properties"]

            for name in properties.keys() - set(schema.get("required", ())):
                _forbid_empty_string(properties[name])

            schema["required"] = list(properties)

        return schema


def _forbid_empty_string(schema: dict[str, Any]) -> None:
    """Require a non-empty string in the string member of a nullable *schema*, so null stays the only way to omit it."""
    members: list[dict[str, Any]] = schema.get("anyOf", [])

    if {"type": "null"} not in members:
        return

    for member in members:
        if member.get("type") == "string":
            _ = member.setdefault("minLength", 1)


# Wins over the family transformer, such as Qwen's, which inlines definitions
# but marks every schema strict without requiring its properties.  pydantic-ai's
# `OpenAIJsonSchemaTransformer` would drop `strict` from every tool with an
# optional argument, or close a free-form object such as a query record.
_VLLM_PROFILE = ModelProfile(json_schema_transformer=StrictJsonSchemaTransformer)

MODEL_RETRIES = RetryOnModelErrors[Any](
    retry_on=(*TRANSIENT_MODEL_ERRORS, ModelEmptyResponseError)
)
"""Retries a request on a transient model error and on a self-hosted runtime's empty reply, which every agent carries.

Not on an unusable reply, which the same request mostly gets again.
"""

# The `<system>` framing is announced from a conversation's first request, so
# the cached prompt prefix never changes once a turn first needs it.
_MODEL_SETTINGS = ModelSettings(framing_announcement="always")


def _bare_model_name(model: str) -> str:
    """The served model name without any ``<org>/`` prefix, casefolded."""
    return model.rsplit("/", maxsplit=1)[-1].casefold()


def create_openai_chat_model(
    model: str,
    provider: LlmProviderSettings,
    *,
    family: ModelFamily | None = None,
    context_window: int | None = None,
    http_client: httpx2.AsyncClient | None = None,
) -> OpenAIChatModel:
    """Build an :class:`OpenAIChatModel` bound to *http_client*, the shared client of the endpoint when ``None``.

    The provider profiles the model by its served name
    (:func:`create_openai_provider`), the vLLM and Ollama ones by the families
    they serve, the OpenAI-compatible one by a ``<vendor>/`` prefix.  On top of
    it go the configured *family* where the runtime takes one
    (``FAMILY_PROVIDERS``), the self-hosted profile on a self-hosted runtime,
    and the configured *context_window*, which pydantic-ai otherwise knows
    only for the models of genai-prices.  OpenAI's own API keeps pydantic-ai's
    profile untouched, since every override would contradict it.
    """
    runtime = provider.inference_provider
    family_profile = (
        _FAMILY_PROFILES[family](_bare_model_name(model))
        if family is not None and runtime in FAMILY_PROVIDERS
        else None
    )
    profile = merge_profile(
        family_profile,
        _SELF_HOSTED_PROFILE if runtime in _SELF_HOSTED else None,
        _VLLM_PROFILE if runtime is InferenceProvider.VLLM else None,
        ModelProfile(context_window=context_window) if context_window else None,
    )

    return OpenAIChatModel(
        model,
        provider=create_openai_provider(provider, http_client),
        profile=profile or None,
        settings=_MODEL_SETTINGS,
    )


def model_from_config(config: LlmConfig) -> OpenAIChatModel:
    """Build an :class:`OpenAIChatModel` from a catalog model.

    The one adapter from the catalog to the pydantic-ai model surface, which
    keeps every call site honest about which fields flow where.
    """
    return create_openai_chat_model(
        config.model,
        config.provider,
        family=config.family,
        context_window=config.context_window,
    )


_BUDGET_FIELDS: dict[InferenceProvider, str] = {
    InferenceProvider.LLAMA_CPP: "thinking_budget_tokens",
    InferenceProvider.VLLM: "thinking_token_budget",
}
"""The request field capping reasoning per runtime, which only these enforce."""

_TEMPLATE_PROVIDERS = frozenset(
    {InferenceProvider.LLAMA_CPP, InferenceProvider.VLLM, InferenceProvider.SGLANG}
)
"""The runtimes switching thinking through ``chat_template_kwargs``, Ollama maps ``reasoning_effort`` itself."""


def thinking_model_settings(
    config: LlmConfig, answer_tokens: int | None = None
) -> ModelSettings:
    """Model settings applying ``config.thinking`` and *max_tokens* across endpoints.

    The one place every model call of the backend takes its reasoning from.
    Spec-compliant OpenAI servers receive the unified pydantic-ai
    ``thinking`` value as the top-level ``reasoning_effort`` field, remapped by
    ``config.reasoning_overrides`` to the level the model actually implements.
    The generic effort selects the hard reasoning token cap.

    Provider-specific request construction is selected by
    ``config.provider.inference_provider``.  llama.cpp and vLLM receive their respective
    hard-budget field plus ``enable_thinking``, SGLang only the latter, while
    Ollama and the OpenAI-compatible APIs receive no extra body fields.

    A ``thinking`` of ``False`` disables reasoning, and a level the model does
    not list is clamped to the nearest one it does, or disables reasoning for a
    model that lists none, while a model that sets no list takes it unchanged.

    The completion is capped by ``config.max_tokens`` and by *answer_tokens*
    plus the reasoning budget where the runtime enforces one, since it counts
    reasoning against the cap, so a reasoning call keeps its whole answer.
    """
    thinking = config.thinking and (
        clamp_reasoning(config.thinking, config.reasoning) or False
    )
    provider = config.provider.inference_provider
    budget_field = _BUDGET_FIELDS.get(provider)
    budget = (
        0
        if thinking is False or budget_field is None
        else settings.llm.thinking_budget_tokens[thinking]
    )
    model_settings = ModelSettings(
        thinking=False
        if thinking is False
        else config.reasoning_overrides.get(thinking, thinking)
    )
    caps = [
        cap
        for cap in (
            config.max_tokens,
            None if answer_tokens is None else answer_tokens + budget,
        )
        if cap is not None
    ]

    if caps:
        model_settings["max_tokens"] = min(caps)

    if provider in _TEMPLATE_PROVIDERS:
        extra_body: dict[str, object] = {
            "chat_template_kwargs": {"enable_thinking": thinking is not False}
        }

        if budget_field is not None and budget:
            extra_body[budget_field] = budget

        model_settings["extra_body"] = extra_body

    return model_settings


# Completion cap for a one-shot summary request: a ceiling that fits a
# structured handover rather than a paragraph, sized against what other
# harnesses budget (see `backend/README.md`).
SUMMARY_MAX_TOKENS = 8192


def summary_model_settings(config: LlmConfig) -> ModelSettings:
    """Settings of a one-shot summary request, reasoning off under a bounded completion.

    Summarizing renders a near-overflowing transcript into a structured note,
    where reasoning adds little and could spend the whole cap on thinking
    before the summary starts.
    """
    return thinking_model_settings(replace(config, thinking=False), SUMMARY_MAX_TOKENS)


# Deliberately not ``agents.app.title_agent``, so converters can run it without
# importing the agents.  The guards that agent carries have nothing to act on in
# a single tool-free completion but its retries of a transient model error, and
# callers cap the images they send themselves.
_completion_agent: Agent[None, str] = Agent(
    name="complete",
    retries=settings.llm.retries,
    model_settings=ModelSettings(timeout=settings.llm.request_timeout_seconds),
    capabilities=[MODEL_RETRIES],
)


async def complete[T](
    prompt: Sequence[UserContent],
    config: LlmConfig,
    *,
    output_type: type[T],
    timeout: float,
    answer_tokens: int | None = None,
    model: Model | None = None,
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
    conversation_id: str | None = None,
    capabilities: Sequence[AbstractCapability[None]] = (),
) -> T:
    """Run one tool-free completion of *output_type*, reasoning as ``config.thinking`` says.

    Meant for single-shot work such as captioning an image, which runs on the
    ``aux`` model with thinking off, or one item of a sandboxed program's
    loop, which runs on the turn's model.
    ``config.max_tokens`` and *answer_tokens* bound the output, see
    :func:`thinking_model_settings`, and *timeout* the seconds spent
    across every retry, raising ``TimeoutError``.  A caller making many calls
    passes the *model* it built from *config* once.

    A caller inside an agent run passes its ``usage`` so the call counts there,
    and then its *usage_limits* too, since pydantic-ai's default would check
    the shared count against a limit of its own, and its *conversation_id* so
    a trace groups the call with the run that made it, and any *capabilities*
    that guard the call's requests.  The call is an `exar` invocation, below
    the run that made it if any, whose usage its own spans record.
    """
    completion = PydanticAiAgent[Sequence[UserContent], T](
        run=lambda task: _completion_agent.run(
            task,
            output_type=output_type,
            model=model or model_from_config(config),
            model_settings=thinking_model_settings(config, answer_tokens),
            usage=usage,
            usage_limits=usage_limits,
            conversation_id=conversation_id,
            capabilities=capabilities,
        ),
        name="complete",
    )

    async with asyncio.timeout(timeout):
        return await call(completion, prompt)
