"""LLM client construction helpers."""

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings, ThinkingLevel

from .http_client import get_http_client
from .types import LlmConfig

__all__ = [
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
    "model_from_config",
    "thinking_model_settings",
]


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
    # effort reaches the server as ``reasoning_effort``.
    return OpenAIChatModel(
        model,
        provider=provider,
        profile=lambda name: OpenAIModelProfile(supports_thinking=True).update(
            provider.model_profile(name)
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


def thinking_model_settings(thinking: ThinkingLevel, config: LlmConfig) -> ModelSettings:
    """Model settings applying *thinking* across heterogeneous endpoints.

    Spec-compliant OpenAI servers receive the unified pydantic-ai
    ``thinking`` value (sent as ``reasoning_effort``).  llama.cpp and vLLM
    ignore that field entirely; their only per-request switch is the
    ``enable_thinking`` chat-template kwarg, which overrides the
    server-side default (e.g. llama.cpp's ``--reasoning on``).  The kwarg
    is only added for self-hosted endpoints (``base_url`` set) — the real
    OpenAI API rejects unknown body fields.
    """
    settings = ModelSettings(thinking=thinking)
    if config.base_url:
        settings["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": thinking is not False}
        }
    return settings
