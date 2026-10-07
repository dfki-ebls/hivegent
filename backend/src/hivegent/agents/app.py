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
from .guards import (
    EnglishToolCalls,
    IncompleteToolCallGuard,
    PromptImageLimit,
    ToolOutputSpill,
)

__all__ = [
    "explore_agent",
    "summary_agent",
    "title_agent",
    "turn_usage_limits",
    "user_agent",
]

_default_model_settings = ModelSettings(
    timeout=settings.llm.request_timeout_seconds,
)

# Carried by the agents rather than composed per run: a run-level
# ``capabilities`` argument adds to these rather than replacing them, so every
# run is guarded, including the subagent and MCP ones that compose their own.
_guards = [
    EnglishToolCalls(),
    IncompleteToolCallGuard(),
    PromptImageLimit(max_images=settings.multimodal.max_images),
]

# Every run over the user's deps bounds its tool returns the same way, the
# chat run and its MCP servers, its subagents, and the MCP exploration alike.
# pydantic-ai orders capabilities across the agent and the run layer as one
# list, so the chat run's `ApprovalNotes` still wraps it.
_spill = ToolOutputSpill(max_chars=settings.llm.tool_output_max_chars)


def _user_agent(name: str) -> Agent[UserDeps, str]:
    """An agent over the user's deps, named for the work it is spent on.

    One agent per purpose rather than one shared, since the name is what a
    trace attributes a run's spend to, and every run is composed per call
    anyway, so the agents differ in nothing else.
    """
    return Agent(
        name=name,
        deps_type=UserDeps,
        retries=settings.llm.retries,
        model_settings=_default_model_settings,
        tool_timeout=settings.llm.tool_timeout_seconds,
        capabilities=[*_guards, _spill],
    )


user_agent = _user_agent("chat")
explore_agent = _user_agent("explore")
"""Runs a delegated exploration, from the ``explore`` tool or the MCP endpoint."""

summary_agent = _user_agent("summarize")
"""Compacts a conversation, or recovers what a failed subagent found."""

title_agent: Agent[None, str] = Agent(
    name="title",
    retries=settings.llm.retries,
    model_settings=_default_model_settings,
    tool_timeout=settings.llm.tool_timeout_seconds,
    capabilities=_guards,
)


# Per-turn request/tool-call bounds shared by the chat agent and its subagents.
# A subagent runs on the parent turn's ``usage`` and inherits its
# ``ctx.usage_limits``, so these bound the whole turn (main agent plus every
# subagent) collectively, without them each run only inherits pydantic-ai's
# implicit default of 50 requests and no tool-call cap.  Built once at import
# like ``_default_model_settings``, since ``UsageLimits`` is read-only run
# config (pydantic-ai mutates ``usage``, never ``limits``).
turn_usage_limits = UsageLimits(
    request_limit=settings.llm.request_limit,
    tool_calls_limit=settings.llm.tool_calls_limit,
)
