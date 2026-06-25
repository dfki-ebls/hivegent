"""LLM client construction helpers."""

from collections.abc import Mapping

from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import merge_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings, ThinkingLevel

from .http_client import get_http_client
from .types import LlmConfig

__all__ = [
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
    "is_context_overflow",
    "model_from_config",
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


def is_context_overflow(error: Exception) -> bool:
    """Whether *error* means the prompt overflowed the model's context window.

    Classifies on the structure the exception offers first: providers
    reject an oversized prompt with a 400 whose body carries a stable
    identifier (see :data:`_CONTEXT_OVERFLOW_CODES`). Message matching
    remains only where no structure exists: vLLM's and SGLang's 400
    bodies are plain prose, and pydantic-ai collapses an empty response
    with ``finish_reason == "length"`` into the message of an
    ``UnexpectedModelBehavior``.
    """
    match error:
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
    allow_private_base_url: bool = False,
) -> OpenAIProvider:
    """Create an OpenAI provider bound to the matching shared HTTP client."""
    return OpenAIProvider(
        api_key=api_key or None,
        base_url=base_url or None,
        http_client=get_http_client(allow_private=allow_private_base_url),
    )


def create_openai_client(
    *,
    api_key: str | None,
    base_url: str | None,
    allow_private_base_url: bool = False,
) -> AsyncOpenAI:
    """Create an OpenAI SDK client bound to the matching shared HTTP client."""
    return AsyncOpenAI(
        api_key=api_key or None,
        base_url=base_url or None,
        http_client=get_http_client(allow_private=allow_private_base_url),
    )


def create_openai_chat_model(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
    allow_private_base_url: bool = False,
) -> OpenAIChatModel:
    """Build an :class:`OpenAIChatModel` bound to the matching shared client."""
    provider = create_openai_provider(
        api_key=api_key,
        base_url=base_url,
        allow_private_base_url=allow_private_base_url,
    )
    # Self-hosted endpoints serve reasoning models pydantic-ai does not
    # recognise, and the inferred profile then defaults to
    # ``supports_thinking=False`` — which silently strips the unified
    # ``thinking`` setting from every request.  Declare support ourselves
    # (on top of the provider's name-based inference) so the configured
    # effort reaches the server as ``reasoning_effort``.  The profile
    # callback receives the provider's already-resolved default profile;
    # ``merge_profile`` layers our override on top (profiles are now
    # ``TypedDict``, so the old ``.update()`` method is gone).
    return OpenAIChatModel(
        model,
        provider=provider,
        profile=lambda profile: merge_profile(
            profile, OpenAIModelProfile(supports_thinking=True)
        ),
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
        allow_private_base_url=config.base_url_is_trusted,
    )


def thinking_model_settings(
    thinking: ThinkingLevel | None, config: LlmConfig
) -> ModelSettings:
    """Model settings applying *thinking* and *max_tokens* across endpoints.

    Spec-compliant OpenAI servers receive the unified pydantic-ai
    ``thinking`` value (sent as ``reasoning_effort``).  llama.cpp and vLLM
    ignore that field entirely; their only per-request switch is the
    ``enable_thinking`` chat-template kwarg, which overrides the
    server-side default (e.g. llama.cpp's ``--reasoning on``).  The kwarg
    is only added for self-hosted endpoints (``base_url`` set) — the real
    OpenAI API rejects unknown body fields.

    *thinking* of ``None`` omits both fields so the server-side default
    decides (the "auto" reasoning level).  ``config.max_tokens`` (resolved
    per tier in :func:`resolve_llm_config`) is forwarded as the completion
    cap whenever set, regardless of the thinking level.
    """
    settings = ModelSettings()
    if config.max_tokens is not None:
        settings["max_tokens"] = config.max_tokens
    if thinking is not None:
        settings["thinking"] = thinking
        if config.base_url:
            settings["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": thinking is not False}
            }
    return settings
