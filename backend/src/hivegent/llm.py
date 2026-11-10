"""LLM client construction helpers, including the quirk compensation that
self-hosted OpenAI-compatible endpoints need."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from openai import AsyncOpenAI
from pydantic_ai import Agent, UserContent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import (
    ModelContextWindowExceededError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.profiles.cohere import cohere_model_profile
from pydantic_ai.profiles.deepseek import deepseek_model_profile
from pydantic_ai.profiles.google import google_model_profile
from pydantic_ai.profiles.harmony import harmony_model_profile
from pydantic_ai.profiles.meta import meta_model_profile
from pydantic_ai.profiles.mistral import mistral_model_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.profiles.zai import zai_model_profile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.vllm import VLLMProvider
from pydantic_ai.settings import ModelSettings, ThinkingEffort
from pydantic_ai.usage import RunUsage, UsageLimits

from .config import InferenceProvider, settings
from .http_client import get_trusted_http_client, get_user_http_client
from .llm_config import LlmConfig, ReasoningEffort

__all__ = [
    "SUMMARY_MAX_TOKENS",
    "capped_tokens",
    "complete",
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
    "is_context_overflow",
    "model_from_config",
    "summary_model_settings",
    "thinking_model_settings",
]

# The error ``type`` llama.cpp rejects an oversized prompt with.
_LLAMACPP_OVERFLOW_TYPE = "exceed_context_size_error"
# SGLang's wordings, whose 400 body carries no stable identifier:
# - "The input (N tokens) is longer than the model's context length (M tokens)."
# - "Input length (N tokens) exceeds the maximum allowed length (M tokens).",
#   the KV-pool bound below the context length.
_CONTEXT_OVERFLOW_PHRASES = (
    "the model's context length",
    "exceeds the maximum allowed length",
)


def is_context_overflow(error: Exception) -> bool:
    """Whether *error* means the prompt overflowed the model's context window.

    pydantic-ai already files OpenAI's ``context_length_exceeded`` code and
    vLLM's wording as ``ModelContextWindowExceededError``.  What it misses is
    matched here: llama.cpp's error type, SGLang's prose, and the empty
    response with ``finish_reason == "length"`` that pydantic-ai collapses into
    the message of an ``UnexpectedModelBehavior``.
    """
    match error:
        case ModelContextWindowExceededError():
            return True
        case ModelHTTPError(status_code=400, provider_error_type=kind, body=body):
            return kind == _LLAMACPP_OVERFLOW_TYPE or any(
                phrase in str(body) for phrase in _CONTEXT_OVERFLOW_PHRASES
            )
        case UnexpectedModelBehavior(message=message):
            return "exceeded before any response was generated" in message
        case _:
            return False


def create_openai_provider(
    *,
    api_key: str | None,
    base_url: str | None,
    base_url_is_trusted: bool,
) -> OpenAIProvider:
    """Create an OpenAI provider bound to the matching shared HTTP client."""
    return OpenAIProvider(
        api_key=api_key or None,
        base_url=base_url or None,
        http_client=(
            get_trusted_http_client() if base_url_is_trusted else get_user_http_client()
        ),
    )


def create_openai_client(
    *,
    api_key: str | None,
    base_url: str | None,
    base_url_is_trusted: bool,
) -> AsyncOpenAI:
    """Create an OpenAI SDK client bound to the matching shared HTTP client."""
    return create_openai_provider(
        api_key=api_key,
        base_url=base_url,
        base_url_is_trusted=base_url_is_trusted,
    ).client


# Model families llama.cpp serves, the same map pydantic-ai maintains in
# ``VLLMProvider.model_profile``, which is not reusable here: it merges vLLM's
# own server quirks on top of it.
_FAMILY_PROFILES: Mapping[str, Callable[[str], ModelProfile | None]] = {
    "llama": meta_model_profile,
    "gemma": google_model_profile,
    "qwen": qwen_model_profile,
    "qwq": qwen_model_profile,
    "deepseek": deepseek_model_profile,
    "mistral": mistral_model_profile,
    "command": cohere_model_profile,
    "c4ai-command": cohere_model_profile,
    "gpt-oss": harmony_model_profile,
    "glm": zai_model_profile,
}

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


def _bare_model_name(model: str) -> str:
    """The served model name without any ``<org>/`` prefix, casefolded."""
    return model.rsplit("/", maxsplit=1)[-1].casefold()


def _match_prefix[T](bare_name: str, table: Mapping[str, T]) -> T | None:
    """The value of the first *table* key that prefixes *bare_name*."""
    return next(
        (value for prefix, value in table.items() if bare_name.startswith(prefix)),
        None,
    )


def _llama_cpp_profile(model: str) -> ModelProfile:
    """The family profile of *model* under the self-hosted one.

    The OpenAI provider profiles by served name, so it hands every self-hosted
    model the OpenAI family's, which keeps the ``$ref``/``$defs`` a local
    runtime's guided decoding wants inlined.  The family profile puts the real
    family back, and the self-hosted one goes on top of both.
    """
    bare_name = _bare_model_name(model)
    family = _match_prefix(bare_name, _FAMILY_PROFILES)

    return merge_profile(family(bare_name) if family else None, _SELF_HOSTED_PROFILE)


def create_openai_chat_model(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
    inference_provider: InferenceProvider | None,
    base_url_is_trusted: bool,
) -> OpenAIChatModel:
    """Build an :class:`OpenAIChatModel` bound to the matching shared client.

    One branch per runtime, so what each endpoint receives is readable in one
    place and a provider added to the enum fails to type-check until it states
    its own quirks.  A spec-compliant endpoint keeps pydantic-ai's own model
    and profile untouched: every override either contradicts what the real
    OpenAI API does or replaces inference already correct there.  ``None``
    means the config was never resolved and is read as that strict contract,
    the same way :func:`_reasoning_extra_body` reads it.

    vLLM gets pydantic-ai's own provider, whose profile already names the
    families it serves and merges the leading ``system`` messages its chat
    templates reject.
    """
    provider = create_openai_provider(
        api_key=api_key,
        base_url=base_url,
        base_url_is_trusted=base_url_is_trusted,
    )
    match inference_provider:
        case None | InferenceProvider.OPENAI:
            return OpenAIChatModel(model, provider=provider)

        case InferenceProvider.LLAMA_CPP:
            return OpenAIChatModel(
                model, provider=provider, profile=_llama_cpp_profile(model)
            )

        case InferenceProvider.VLLM:
            return OpenAIChatModel(
                model,
                provider=VLLMProvider(openai_client=provider.client),
                profile=_SELF_HOSTED_PROFILE,
            )


def model_from_config(config: LlmConfig) -> OpenAIChatModel:
    """Build an :class:`OpenAIChatModel` from an :class:`LlmConfig`.

    Single canonical adapter from the pydantic-validated request shape to
    the pydantic-ai model surface — keeps every call site honest about
    which fields flow where.
    """
    return create_openai_chat_model(
        config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        inference_provider=config.inference_provider,
        base_url_is_trusted=config.base_url_is_trusted,
    )


# Generic-to-native effort remapping for models whose reasoning levels do not
# line up with pydantic-ai's, keyed by a casefolded model-name prefix (the part
# after any ``<org>/``).  Levels a model does not implement, such as Qwen3.8's
# missing ``high``, are mapped onto the nearest one it does, while the
# configured thinking budget stays keyed on the generic level.
_REASONING_EFFORT_OVERRIDES: Mapping[str, Mapping[ReasoningEffort, ThinkingEffort]] = {
    "qwen3.8": {"high": "xhigh"},
}


def _map_reasoning_effort(model: str, effort: ReasoningEffort) -> ThinkingEffort:
    overrides = _match_prefix(_bare_model_name(model), _REASONING_EFFORT_OVERRIDES)

    return overrides.get(effort, effort) if overrides else effort


def _reasoning_extra_body(
    thinking: ReasoningEffort | Literal[False], provider: InferenceProvider | None
) -> dict[str, object] | None:
    match provider:
        case InferenceProvider.LLAMA_CPP:
            budget_field = "thinking_budget_tokens"
        case InferenceProvider.VLLM:
            budget_field = "thinking_token_budget"
        case InferenceProvider.OPENAI | None:
            return None

    extra_body: dict[str, object] = {
        "chat_template_kwargs": {"enable_thinking": thinking is not False}
    }
    if thinking is not False:
        extra_body[budget_field] = settings.llm.thinking_budget_tokens[thinking]

    return extra_body


def thinking_model_settings(
    thinking: ReasoningEffort | Literal[False], config: LlmConfig
) -> ModelSettings:
    """Model settings applying *thinking* and *max_tokens* across endpoints.

    Spec-compliant OpenAI servers receive the unified pydantic-ai
    ``thinking`` value as the top-level ``reasoning_effort`` field, remapped by
    :data:`_REASONING_EFFORT_OVERRIDES` to the level the model actually
    implements.  The generic effort selects the hard reasoning token cap.

    Provider-specific request construction is selected by
    ``config.inference_provider``.  llama.cpp and vLLM receive their respective
    hard-budget field plus ``enable_thinking``, while OpenAI receives no extra
    body fields.

    *thinking* of ``False`` disables reasoning.  ``config.max_tokens``
    (resolved per tier in :func:`resolve_llm_config`) is forwarded as the
    completion cap whenever set, regardless of the thinking level.
    """
    model_settings = ModelSettings(
        thinking=False
        if thinking is False
        else _map_reasoning_effort(config.model, thinking)
    )

    if config.max_tokens is not None:
        model_settings["max_tokens"] = config.max_tokens

    extra_body = _reasoning_extra_body(thinking, config.inference_provider)
    if extra_body is not None:
        model_settings["extra_body"] = extra_body

    return model_settings


# Completion cap for a one-shot summary request: a ceiling that fits a
# structured handover rather than a paragraph, sized against what other
# harnesses budget (see `backend/README.md`).  Its other job is to stop a
# reasoning model from spending the whole provider-default budget on thinking
# and returning a length-truncated empty response.
SUMMARY_MAX_TOKENS = 8192


def capped_tokens(config: LlmConfig, cap: int) -> int:
    """*cap*, or the configured ``max_tokens`` where that is lower.

    A ``max_tokens`` the endpoint would reject is not made acceptable by a
    caller with a narrower purpose asking for it.
    """
    return min(config.max_tokens or cap, cap)


def summary_model_settings(config: LlmConfig) -> ModelSettings:
    """Bounded, reasoning-off settings for a one-shot summary request.

    Summarization renders a large (near-overflowing) transcript into a
    structured digest, so reasoning adds little and risks the model emitting
    only thinking until it hits the completion limit — a length-truncated
    empty response the server reports as success.  Disabling thinking and
    capping the completion keeps the whole output budget available for the
    summary itself.  The cap never rises above what the request is already
    configured for (:func:`capped_tokens`).
    """
    model_settings = thinking_model_settings(False, config)
    model_settings["max_tokens"] = capped_tokens(config, SUMMARY_MAX_TOKENS)
    return model_settings


# Deliberately not ``agents.app.title_agent``, so converters can run it without
# importing the agents.  The guards that agent carries have nothing to act on in
# a single tool-free completion, and callers cap the images they send themselves.
_completion_agent: Agent[None, str] = Agent(
    name="complete",
    retries=settings.llm.retries,
    model_settings=ModelSettings(timeout=settings.llm.request_timeout_seconds),
)


async def complete(
    prompt: Sequence[UserContent],
    config: LlmConfig,
    *,
    timeout: float,
    model: Model | None = None,
    usage: RunUsage | None = None,
    usage_limits: UsageLimits | None = None,
    conversation_id: str | None = None,
    capabilities: Sequence[AbstractCapability[None]] = (),
) -> str:
    """Run one tool-free completion with thinking disabled.

    Meant for single-shot work such as converting a page, captioning an image,
    or one item of a sandboxed program's loop, where a reasoning trace would
    only fill a small model's window before any text is emitted.
    ``config.max_tokens`` bounds the output and *timeout* the seconds spent
    across every retry, raising ``TimeoutError``.  A caller making many calls
    passes the *model* it built from *config* once.

    A caller inside an agent run passes its ``usage`` so the call counts there,
    and then its *usage_limits* too, since pydantic-ai's default would check
    the shared count against a limit of its own, and its *conversation_id* so
    a trace groups the call with the run that made it, and any *capabilities*
    that guard the call's requests.
    """
    async with asyncio.timeout(timeout):
        result = await _completion_agent.run(
            prompt,
            model=model or model_from_config(config),
            model_settings=thinking_model_settings(False, config),
            usage=usage,
            usage_limits=usage_limits,
            conversation_id=conversation_id,
            capabilities=capabilities,
        )

    return result.output
