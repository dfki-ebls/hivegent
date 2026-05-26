"""LLM client construction helpers."""

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .http_client import get_shared_http_client
from .types import LlmConfig

__all__ = [
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
    "model_from_config",
]


def create_openai_provider(
    *,
    api_key: str | None,
    base_url: str | None,
) -> OpenAIProvider:
    """Create an OpenAI provider bound to the shared safe HTTP client."""
    return OpenAIProvider(
        api_key=api_key or None,
        base_url=base_url or None,
        http_client=get_shared_http_client(),
    )


def create_openai_client(
    *,
    api_key: str | None,
    base_url: str | None,
) -> AsyncOpenAI:
    """Create an OpenAI SDK client bound to the shared safe HTTP client."""
    return AsyncOpenAI(
        api_key=api_key or None,
        base_url=base_url or None,
        http_client=get_shared_http_client(),
    )


def create_openai_chat_model(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
) -> OpenAIChatModel:
    """Build an :class:`OpenAIChatModel` bound to the shared safe HTTP client."""
    return OpenAIChatModel(
        model,
        provider=create_openai_provider(api_key=api_key, base_url=base_url),
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
    )
