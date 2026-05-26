"""Agent assembly for Hivegent.

The agent-level ``model_settings`` here is the single source of truth
for project-wide LLM defaults (currently: request timeout).  Pydantic-AI
runs ``merge_model_settings`` between the agent default and any
per-call ``model_settings`` argument, so per-call overrides (e.g.
``ModelSettings(thinking=False)`` on the document converter) layer on
top without losing the default timeout.  Add new global defaults here,
not at every call site.
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
