"""Agent assembly for Hivegent.

Project-wide LLM defaults live on ``model_settings`` here; pydantic-ai
merges per-call ``model_settings`` on top, so add new global defaults
in this file rather than at each call site.
"""

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from ..config import settings
from .common import UserDeps

__all__ = ["base_agent", "user_agent"]

_default_model_settings = ModelSettings(
    timeout=settings.network.llm_request_timeout_seconds,
)

base_agent: Agent[None, str] = Agent(
    retries=1, model_settings=_default_model_settings
)
user_agent: Agent[UserDeps, str] = Agent(
    deps_type=UserDeps, retries=1, model_settings=_default_model_settings
)
