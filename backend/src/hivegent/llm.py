"""LLM client construction helpers, including the quirk compensation that
self-hosted OpenAI-compatible endpoints need."""

from collections.abc import Callable, Iterable, Mapping
from typing import override

from openai import AsyncOpenAI
from openai.types.chat import chat_completion_chunk
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelResponseStreamEvent, PartStartEvent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIStreamedResponse
from pydantic_ai.profiles import ModelProfile, ModelProfileSpec, merge_profile
from pydantic_ai.profiles.cohere import cohere_model_profile
from pydantic_ai.profiles.deepseek import deepseek_model_profile
from pydantic_ai.profiles.google import google_model_profile
from pydantic_ai.profiles.harmony import harmony_model_profile
from pydantic_ai.profiles.meta import meta_model_profile
from pydantic_ai.profiles.mistral import mistral_model_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings, ThinkingEffort, ThinkingLevel

from .config import InferenceProvider
from .http_client import get_trusted_http_client, get_user_http_client
from .llm_config import LlmConfig, ReasoningEffort

__all__ = [
    "AUTO_REASONING_EFFORT",
    "SUMMARY_MAX_TOKENS",
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
    "is_context_overflow",
    "model_from_config",
    "resolve_thinking",
    "summary_model_settings",
    "thinking_model_settings",
]

# Stable identifiers providers attach to an oversized-prompt rejection:
# OpenAI puts ``context_length_exceeded`` in ``code``, llama.cpp puts
# ``exceed_context_size_error`` in ``type``.
_CONTEXT_OVERFLOW_CODES = frozenset(
    {"context_length_exceeded", "exceed_context_size_error"}
)
# Prose fallbacks for servers whose 400 body carries no stable
# identifier (a numeric ``code`` and a generic exception-class ``type``):
# - vLLM: "This model's maximum context length is N tokens." — also
#   covers SGLang's total-token variant ("Requested token count exceeds
#   the model's maximum context length of N tokens.").
# - SGLang: "The input (N tokens) is longer than the model's context
#   length (M tokens)."
# - SGLang's scheduler: "Input length (N tokens) exceeds the maximum
#   allowed length (M tokens)." (the KV-pool bound below the context
#   length).
_CONTEXT_OVERFLOW_PHRASES = (
    "maximum context length",
    "the model's context length",
    "exceeds the maximum allowed length",
)


class _SegmentedOpenAIStreamedResponse(OpenAIStreamedResponse):
    """Start a new text part when content resumes after a tool call.

    vLLM's ``qwen3_xml`` parser returns to content mode after ``</tool_call>``,
    so prose (or just the trailing newline) streams as ``content`` deltas that
    follow the ``tool_calls`` ones.  pydantic-ai keys all text at the fixed
    ``'content'`` vendor id, so that tail is appended to the ``TextPart`` its
    own stream layer already closed: the run's message list reorders the answer
    around the tool call, and the Vercel adapter emits a ``text-delta`` for a
    text part the frontend has ended, which is a hard error there.  Untracking
    the vendor id is how pydantic-ai itself ends a text run (see
    ``_handle_embedded_thinking_end``), and it fixes both symptoms at once —
    a guard in ``ChatEventStream`` would repair only the wire.

    Scoped to tool calls: text resuming after a *separate-field* thinking part
    would need the same treatment, but no reasoning parser we serve orders a
    response that way.
    """

    @override
    def _map_tool_call_delta(
        self, choice: chat_completion_chunk.Choice
    ) -> Iterable[ModelResponseStreamEvent]:
        for event in super()._map_tool_call_delta(choice):
            if isinstance(event, PartStartEvent):
                self._parts_manager._stop_tracking_vendor_id("content")

            yield event


class _SegmentedOpenAIChatModel(OpenAIChatModel):
    """Model that streams through :class:`_SegmentedOpenAIStreamedResponse`."""

    @property
    @override
    def _streamed_response_cls(self) -> type[OpenAIStreamedResponse]:
        return _SegmentedOpenAIStreamedResponse


def is_context_overflow(error: Exception) -> bool:
    """Whether *error* means the prompt overflowed the model's context window.

    Classifies on the structure the exception offers first: providers
    reject an oversized prompt with a 400 whose body carries a stable
    identifier (see :data:`_CONTEXT_OVERFLOW_CODES`), and a completion that
    ran out of room mid tool call is pydantic-ai's ``IncompleteToolCall``
    (raised by :class:`~hivegent.agents.guards.IncompleteToolCallGuard`
    before the call is dispatched, and by pydantic-ai itself once the tool's
    retry budget is spent). Message matching remains only where no structure
    exists: vLLM's and SGLang's 400 bodies are plain prose, and pydantic-ai
    collapses an empty response with ``finish_reason == "length"`` into the
    message of an ``UnexpectedModelBehavior``.
    """
    match error:
        case IncompleteToolCall():
            return True
        case ModelHTTPError(status_code=400, body=body):
            if isinstance(body, Mapping) and (
                body.get("code") in _CONTEXT_OVERFLOW_CODES
                or body.get("type") in _CONTEXT_OVERFLOW_CODES
            ):
                return True
            return any(phrase in str(body) for phrase in _CONTEXT_OVERFLOW_PHRASES)
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


# Model families a self-hosted runtime serves, borrowed from
# https://github.com/pydantic/pydantic-ai/pull/6153.  pydantic-ai ships the same
# map in ``OllamaProvider.model_profile``, which is not reusable here: it merges
# Ollama's own server quirks on top of it.
_FAMILY_PROFILES: Mapping[str, Callable[[str], ModelProfile | None]] = {
    "llama": meta_model_profile,
    "gemma": google_model_profile,
    "qwen": qwen_model_profile,
    "qwq": qwen_model_profile,
    "deepseek": deepseek_model_profile,
    "mistral": mistral_model_profile,
    "command": cohere_model_profile,
    "gpt-oss": harmony_model_profile,
}


def _bare_model_name(model: str) -> str:
    """The served model name without any ``<org>/`` prefix, casefolded."""
    return model.rsplit("/", maxsplit=1)[-1].casefold()


def _match_prefix[T](bare_name: str, table: Mapping[str, T]) -> T | None:
    """The value of the first *table* key that prefixes *bare_name*."""
    return next(
        (value for prefix, value in table.items() if bare_name.startswith(prefix)),
        None,
    )


def _family_profile(model: str) -> ModelProfile | None:
    """pydantic-ai's profile for the family *model* belongs to, if any."""
    bare_name = _bare_model_name(model)
    family = _match_prefix(bare_name, _FAMILY_PROFILES)

    return family(bare_name) if family else None


def _self_hosted_profile(
    model: str, *, merge_system_messages: bool
) -> ModelProfileSpec:
    """Profile a self-hosted runtime needs on top of the provider's.

    Three layers with three owners: the OpenAI provider profiles by served
    name, so it hands every self-hosted model the OpenAI family's, which keeps
    the ``$ref``/``$defs`` a local runtime's guided decoding wants inlined;
    :func:`_family_profile` puts the real family back; ours goes on top of
    both.  ``supports_thinking`` because these endpoints serve reasoning models
    pydantic-ai cannot name-infer, so its ``False`` would silently strip the
    unified ``thinking`` setting from every request, and
    ``ignore_streamed_leading_whitespace`` because the whitespace is what the
    local chat templates emit around a tool call, whatever a family measured
    against its own hosted endpoint.

    *merge_system_messages* answers the Qwen chat template as vLLM applies it,
    which rejects a second ``system`` message with "System message must be at
    the beginning.", while per-capability instructions render as one each.  It
    is keyed on the runtime rather than the family because a vLLM deployment
    applies that template whether or not the served name carries ``qwen``.
    """
    overrides = OpenAIModelProfile(
        supports_thinking=True,
        ignore_streamed_leading_whitespace=True,
    )
    if merge_system_messages:
        overrides["openai_chat_supports_multiple_system_messages"] = False

    family = _family_profile(model)

    return lambda profile: merge_profile(profile, family, overrides)


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

    The segmented stream answers vLLM's ``qwen3_xml`` parser returning to
    content mode after a tool call; llama.cpp's leaves message order alone.
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
                model,
                provider=provider,
                profile=_self_hosted_profile(model, merge_system_messages=False),
            )

        case InferenceProvider.VLLM:
            return _SegmentedOpenAIChatModel(
                model,
                provider=provider,
                profile=_self_hosted_profile(model, merge_system_messages=True),
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


# ``auto`` is a stable public alias for the deployed default effort — kept in
# the API enum so the default can be retargeted with a single edit here,
# without touching clients or the request schema.  It resolves to ``high``.
AUTO_REASONING_EFFORT: ThinkingEffort = "medium"


def resolve_thinking(effort: ReasoningEffort) -> ThinkingLevel:
    """Resolve an API reasoning effort to a pydantic-ai thinking level.

    ``auto`` resolves to :data:`AUTO_REASONING_EFFORT`; ``none`` disables
    reasoning (``False``); every explicit level (``minimal``…``xhigh``)
    passes through unchanged.
    """
    if effort == "auto":
        return AUTO_REASONING_EFFORT

    if effort == "none":
        return False

    return effort


# Per-request reasoning caps for self-hosted endpoints, keyed by the generic
# effort level before any model-specific mapping.  llama.cpp and vLLM force
# the reasoning block closed at the cap without truncating the answer.
# ``xhigh`` is intentionally absent so it runs unbounded; the ``False``
# (``none``) sentinel never indexes it.  llama.cpp honours a request cap only
# while ``--reasoning-budget`` remains at its -1 default.  vLLM requires a
# reasoning parser and runner with thinking-budget support.
_THINKING_BUDGET_TOKENS: Mapping[ThinkingEffort, int] = {
    "minimal": 512,
    "low": 2048,
    "medium": 6144,
    "high": 16384,
}


# Generic-to-native effort remapping for models whose reasoning levels do not
# line up with pydantic-ai's, keyed by a casefolded model-name prefix (the part
# after any ``<org>/``).  Levels a model does not implement are mapped onto the
# nearest one it does; the budget above stays keyed on the generic level, so
# two levels mapping to the same native one keep distinct caps.
_REASONING_EFFORT_OVERRIDES: Mapping[str, Mapping[ThinkingEffort, ThinkingEffort]] = {
    "qwen3.8": {"minimal": "low", "high": "medium"},
}


def _map_reasoning_effort(model: str, effort: ThinkingEffort) -> ThinkingEffort:
    overrides = _match_prefix(_bare_model_name(model), _REASONING_EFFORT_OVERRIDES)

    return overrides.get(effort, effort) if overrides else effort


def _reasoning_extra_body(
    thinking: ThinkingLevel, provider: InferenceProvider | None
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
    budget = (
        _THINKING_BUDGET_TOKENS.get(thinking) if isinstance(thinking, str) else None
    )
    if budget is not None:
        extra_body[budget_field] = budget

    return extra_body


def thinking_model_settings(
    thinking: ThinkingLevel | None, config: LlmConfig
) -> ModelSettings:
    """Model settings applying *thinking* and *max_tokens* across endpoints.

    Spec-compliant OpenAI servers receive the unified pydantic-ai
    ``thinking`` value as the top-level ``reasoning_effort`` field, remapped by
    :data:`_REASONING_EFFORT_OVERRIDES` to the level the model actually
    implements.  The original generic effort independently selects a hard
    reasoning token cap, preserving the distinction between levels that map to
    the same native value.

    Provider-specific request construction is selected by
    ``config.inference_provider``.  llama.cpp and vLLM receive their respective
    hard-budget field plus ``enable_thinking``, while OpenAI receives no extra
    body fields.

    *thinking* of ``None`` omits every field so the server-side default
    decides (the "auto" reasoning level).  ``config.max_tokens`` (resolved
    per tier in :func:`resolve_llm_config`) is forwarded as the completion
    cap whenever set, regardless of the thinking level.
    """
    settings = ModelSettings()
    if config.max_tokens is not None:
        settings["max_tokens"] = config.max_tokens
    if thinking is not None:
        settings["thinking"] = (
            _map_reasoning_effort(config.model, thinking)
            if isinstance(thinking, str)
            else thinking
        )
        extra_body = _reasoning_extra_body(thinking, config.inference_provider)
        if extra_body is not None:
            settings["extra_body"] = extra_body
    return settings


# Completion cap for a one-shot summary request: a ceiling that fits a
# structured handover rather than a paragraph, sized against what other
# harnesses budget (see `backend/README.md`).  Its other job is to stop a
# reasoning model from spending the whole provider-default budget on thinking
# and returning a length-truncated empty response.
SUMMARY_MAX_TOKENS = 8192


def summary_model_settings(config: LlmConfig) -> ModelSettings:
    """Bounded, reasoning-off settings for a one-shot summary request.

    Summarization renders a large (near-overflowing) transcript into a
    structured digest, so reasoning adds little and risks the model emitting
    only thinking until it hits the completion limit — a length-truncated
    empty response the server reports as success.  Disabling thinking and
    capping the completion keeps the whole output budget available for the
    summary itself.  The cap never rises above what the request is already
    configured for: a ``max_tokens`` the endpoint would reject is not made
    acceptable by the summary being the one asking for it.
    """
    model_settings = thinking_model_settings(False, config)
    model_settings["max_tokens"] = min(
        SUMMARY_MAX_TOKENS, model_settings.get("max_tokens", SUMMARY_MAX_TOKENS)
    )
    return model_settings
