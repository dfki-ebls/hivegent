"""LLM client construction helpers."""

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .http_client import get_shared_http_client

__all__ = [
    "create_openai_chat_model",
    "create_openai_client",
    "create_openai_provider",
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
