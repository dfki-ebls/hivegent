"""Agent assembly for Hivegent.

Project-wide LLM defaults live on ``model_settings`` here; pydantic-ai
merges per-call ``model_settings`` on top, so add new global defaults
in this file rather than at each call site.
"""

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from ..config import settings
from .common import UserDeps
from .guards import IncompleteToolCallGuard

__all__ = ["base_agent", "turn_usage_limits", "user_agent"]

_default_model_settings = ModelSettings(
    timeout=settings.llm.request_timeout_seconds,
)

# Carried by the agents rather than composed per run: a run-level
# ``capabilities`` argument adds to these rather than replacing them, so every
# run is guarded, including the subagent and MCP ones that compose their own.
_guards = [IncompleteToolCallGuard()]

base_agent: Agent[None, str] = Agent(
    retries=settings.llm.retries,
    model_settings=_default_model_settings,
    tool_timeout=settings.llm.tool_timeout_seconds,
    capabilities=_guards,
)
user_agent: Agent[UserDeps, str] = Agent(
    deps_type=UserDeps,
    retries=settings.llm.retries,
    model_settings=_default_model_settings,
    tool_timeout=settings.llm.tool_timeout_seconds,
    capabilities=_guards,
)


# Per-turn request/tool-call bounds shared by the chat agent and its subagents.
# A subagent runs on the parent turn's ``usage`` accumulator, so applying these
# there bounds the whole turn (main agent plus every subagent) collectively;
# without them each run only inherits pydantic-ai's implicit default of 50
# requests and no tool-call cap.  Built once at import like
# ``_default_model_settings`` — ``UsageLimits`` is read-only run config
# (pydantic-ai mutates ``usage``, never ``limits``), so one shared instance is safe.
turn_usage_limits = UsageLimits(
    request_limit=settings.llm.request_limit,
    tool_calls_limit=settings.llm.tool_calls_limit,
)
