"""The LLM configuration shape and the server defaults applied to it.

Kept out of :mod:`hivegent.types` so :mod:`hivegent.llm` and the converters can
describe an LLM request without importing the API models, which reach back into
the converter registry.  That import cycle is why this module exists.
"""

from typing import Literal, Self

from pydantic import BaseModel, PrivateAttr, model_validator
from pydantic_ai.settings import ThinkingEffort

from .config import InferenceProvider, settings
from .security import require_safe_url_shape

__all__ = ["LlmConfig", "LlmTier", "ReasoningEffort", "resolve_llm_config"]


type ReasoningEffort = Literal["auto", "none"] | ThinkingEffort
"""Reasoning effort accepted from the API.

Combines pydantic-ai's native effort levels (``minimal``/``low``/``medium``/
``high``/``xhigh``) with the ``auto`` (a stable alias for the deployed
default effort) and ``none`` (disable thinking) sentinels.
"""


class LlmConfig(BaseModel):
    """Client-provided LLM configuration overrides.

    User-provided ``base_url`` values run through the SSRF filter.
    Server-configured ``base_url`` values are trusted operator input.
    """

    model: str = ""
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int | None = None
    inference_provider: InferenceProvider | None = None

    _base_url_is_trusted: bool = PrivateAttr(default=False)

    @property
    def base_url_is_trusted(self) -> bool:
        """Whether ``base_url`` came from server configuration."""
        return self._base_url_is_trusted

    @model_validator(mode="after")
    def _check_base_url(self) -> Self:
        if self.base_url:
            require_safe_url_shape(self.base_url, "LLM base_url")
        return self


type LlmTier = Literal["main", "aux"]


def resolve_llm_config(llm: LlmConfig, *, tier: LlmTier = "aux") -> LlmConfig:
    """Apply server defaults to a client-provided LLM configuration.

    *tier* selects which configured ``(model, max_tokens)`` pair backs the
    fields the client left blank, so the two always move together.  The aux
    model falls back to the main model when unset.
    """
    main_tier = tier == "main"
    default_model = settings.llm.model if main_tier else settings.llm.aux_model
    default_max_tokens = (
        settings.llm.max_tokens if main_tier else settings.llm.aux_max_tokens
    )
    configured_base_url = settings.llm.base_url or None
    resolved = LlmConfig(
        model=llm.model or default_model or settings.llm.model,
        api_key=llm.api_key or settings.llm.api_key,
        base_url=llm.base_url or configured_base_url,
        max_tokens=llm.max_tokens or default_max_tokens,
        inference_provider=(llm.inference_provider or settings.llm.inference_provider),
    )
    resolved._base_url_is_trusted = llm.base_url_is_trusted or (
        not llm.base_url and configured_base_url is not None
    )
    return resolved
