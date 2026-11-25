"""Agent assembly for Hivegent.

Project-wide LLM defaults live on ``model_settings`` here; pydantic-ai
merges per-call ``model_settings`` on top, so add new global defaults
in this file rather than at each call site.
"""

from collections.abc import Sequence
from typing import Any

from exar.pydantic_ai import DeclaredEffects
from exar.pydantic_ai.compaction import CompactionWindow
from exar.pydantic_ai.guards import LenientArguments
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities import AbstractCapability, RetryOnModelErrors
from pydantic_ai.output import OutputSpec
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import (
    DetectLoops,
    LabelCrossModelHistory,
    ToolBudgets,
)

from ..config import settings
from .common import UserDeps
from .guards import (
    HOST_GATE,
    INCOMPLETE_TOOL_CALLS,
    OFFERED_TOOLS,
    PROMPT_IMAGE_LIMIT,
    SPILL_TOOLS,
    TOOL_OUTPUT_FENCE,
    VISIONLESS_IMAGES,
    ConversationSpill,
    EnglishToolCalls,
    limit_notes,
)
from .tools.web import WEB_TOOLS

__all__ = [
    "chat_agent",
    "default_model_settings",
    "guards",
    "summary_agent",
    "title_agent",
    "turn_usage_limits",
    "user_agent",
]

default_model_settings = ModelSettings(
    timeout=settings.llm.request_timeout_seconds,
)

# Carried by the agents rather than composed per run: a run-level
# ``capabilities`` argument adds to these rather than replacing them, so every
# run is guarded, including the subagent and MCP ones that compose their own.
# `RetryOnModelErrors` retries a transient model error in place of the SDK's
# own retries, waiting as long as the endpoint asks.
guards = [
    RetryOnModelErrors[Any](),
    EnglishToolCalls(),
    INCOMPLETE_TOOL_CALLS,
    PROMPT_IMAGE_LIMIT,
    limit_notes(settings.llm.tool_output_max_chars),
]

# Every run over the user's deps, the summary included, sends only the history
# from the latest compaction onward.
_window = CompactionWindow()

# Every run over the user's deps bounds its tool returns the same way, the
# chat run and its MCP servers, its subagents, and the MCP exploration alike.
# pydantic-ai orders capabilities across the agent and the run layer as one
# list, so the chat run's `ApprovalNotes` still wraps it.  The model queries a
# saved result with the `SPILL_TOOLS` it adds, or with any tool that reads
# `/tmp`.
_spill = [
    ConversationSpill(
        max_chars=settings.llm.tool_output_max_chars,
        max_data_bytes=settings.llm.tool_data_max_bytes,
    ),
    DeclaredEffects(effects=dict.fromkeys(SPILL_TOOLS, "read")),
]


# A run that repeats itself, across calls or within one streamed response, is
# told to change its approach, and the web is never asked the same twice in a
# run, while the operator's budgets cap the tools they name.
_repetition = [
    DetectLoops[UserDeps](),
    ToolBudgets[UserDeps](per_tool=settings.tools.budgets, refuse_repeats=WEB_TOOLS),
]


def user_agent[O](
    name: str,
    output_type: OutputSpec[O],
    capabilities: Sequence[AbstractCapability[UserDeps]] = (),
    model_settings: ModelSettings = default_model_settings,
) -> Agent[UserDeps, O]:
    """An agent over the user's deps, named for the work it is spent on.

    One agent per purpose rather than one shared, since the name is what a
    trace attributes a run's spend to, and every run is composed per call
    anyway, so the agents differ in nothing else but the *capabilities* that
    compose a subagent, since pydantic-ai-harness runs a delegate with what its
    agent carries, its *model_settings* too, which reason as the turn does.
    Each tells its model when earlier turns came from another one, since a turn
    may pick another model of the catalog than the turns before it.
    """
    return Agent(
        name=name,
        deps_type=UserDeps,
        output_type=output_type,
        retries=settings.llm.retries,
        model_settings=model_settings,
        capabilities=[
            _window,
            *guards,
            OFFERED_TOOLS,
            HOST_GATE,
            LenientArguments(),
            VISIONLESS_IMAGES,
            TOOL_OUTPUT_FENCE,
            *_spill,
            *_repetition,
            LabelCrossModelHistory(),
            *capabilities,
        ],
    )


chat_agent = user_agent("chat", [str, DeferredToolRequests])
"""Runs a chat turn, which ends on an answer or on the calls it awaits approval or answers for."""

summary_agent = user_agent("summarize", str)
"""Compacts a conversation as its next turn (``agents/compaction.py``)."""

title_agent: Agent[None, str] = Agent(
    name="title",
    retries=settings.llm.retries,
    model_settings=default_model_settings,
    capabilities=guards,
)


# The bounds of a chat turn, its main agent and its subagents together, since
# they share the turn's usage (``agents/subagents.py``).  Without them each run
# only inherits pydantic-ai's implicit default of 50 requests and no tool-call
# cap.  Built once at import like ``default_model_settings``, since
# ``UsageLimits`` is read-only run config (pydantic-ai mutates ``usage``, never
# ``limits``).
turn_usage_limits = UsageLimits(
    request_limit=settings.llm.request_limit,
    tool_calls_limit=settings.llm.tool_calls_limit,
    cost_limit=settings.subagents.max_cost,
)
