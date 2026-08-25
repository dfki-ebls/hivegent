"""Subagent-oriented agent tool registrations."""

import asyncio
import logging
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ToolReturn
from pydantic_ai.models import Model

from ...config import settings
from ...llm import is_context_overflow, model_from_config, summary_model_settings
from ...llm_config import LlmConfig, resolve_llm_config
from ...prompts import (
    EXPLORE_INSTRUCTIONS,
    GROUNDING_INSTRUCTIONS,
    VERSION_INSTRUCTIONS,
)
from ...tools.base import ToolOutput
from ...tools.pydantic_ai import wrap_tool_output
from ..app import turn_usage_limits, user_agent
from ..common import ExploreTaskArg, UserDeps, scope_instructions
from ..subagent_events import SubagentTranscriptBuilder, SubagentUpdate
from ..summarize import summarize_messages
from .conversation import conversation_toolset
from .explore import explore_toolset
from .web import web_enabled, web_toolset

logger = logging.getLogger(__name__)

__all__ = [
    "SUBAGENT_CAPABILITIES",
    "SubagentName",
    "explore",
    "run_subagent",
    "subagent_toolset",
]

subagent_toolset: FunctionToolset[UserDeps] = FunctionToolset()

type SubagentName = Literal["documents", "conversations", "web"]

# The shared registry mapping each subagent name to the capability that defines
# it.  Public so every module that delegates to a subagent composes from the same
# capabilities: the ``explore`` tool selects one by name, and the MCP exploration
# endpoint reuses the ``documents`` subagent directly.  Each value is a
# self-contained capability a delegated run is composed from; the ``documents``
# subagent *is* a document-exploration agent, so the explore toolset and its
# system-prompt persona ride together, distinct from the main agent's bare
# ``explore`` bundle.
SUBAGENT_CAPABILITIES: dict[SubagentName, AbstractCapability[UserDeps]] = {
    "documents": Capability(
        id="explore-subagent",
        toolsets=[explore_toolset],
        instructions=[
            EXPLORE_INSTRUCTIONS,
            GROUNDING_INSTRUCTIONS,
            VERSION_INSTRUCTIONS,
            scope_instructions,
        ],
    ),
    "conversations": Capability(id="conversation", toolsets=[conversation_toolset]),
}

# The web subagent only exists while its toolset is live; otherwise a `web`
# scope would delegate to an agent with no tools.  The scope argument's type
# and description drop `web` in lockstep so the model is never offered it.
if web_enabled:
    SUBAGENT_CAPABILITIES["web"] = Capability(id="web", toolsets=[web_toolset])

    type ExploreScope = SubagentName

    _scope_description = (
        "What to explore: `documents` for the document collection, "
        "`conversations` for past chat history, `web` for web research."
    )

else:
    type ExploreScope = Literal["documents", "conversations"]

    _scope_description = (
        "What to explore: `documents` for the document collection, "
        "`conversations` for past chat history."
    )

ExploreScopeArg = Annotated[ExploreScope, Field(description=_scope_description)]


def _subagent_llm_config(deps: UserDeps) -> LlmConfig:
    """Resolve the LLM config for subagent calls.

    Subagents perform agentic exploration with large contexts and tool
    calling, so they reuse the main model rather than ``aux_model`` —
    tiny aux models tend to fail in these scenarios.  A run without a
    client override falls back to the server-configured main tier.
    """
    return deps.llm or resolve_llm_config(LlmConfig(), tier="main")


def _subagent_result(
    tool_call_id: str, builder: SubagentTranscriptBuilder, text: str
) -> ToolReturn:
    """Pack the model-facing *text* with the subagent transcript for the UI.

    The transcript rides on the tool return's metadata as a ``data-tool-output``
    chunk (via :func:`wrap_tool_output`), so it persists and re-renders on
    reload like any other structured tool output; the model only ever sees
    *text*.
    """
    return wrap_tool_output(
        ToolOutput(data=builder.transcript, formatted=text), tool_call_id=tool_call_id
    )


def _failure_reason(exc: Exception) -> str:
    """Describe why a subagent run ended early, for the recovery message."""
    if is_context_overflow(exc):
        return "hit the model's context limit"

    if isinstance(exc, TimeoutError):
        return "ran past its time budget"

    return "failed unexpectedly"


async def _safe_summarize(
    messages: Sequence[ModelMessage], model: Model, llm_config: LlmConfig
) -> str | None:
    """Summarize the partial transcript, returning ``None`` if even that fails.

    A subagent crash can stem from the model endpoint itself, so the
    recovery summary (another call to the same model) may fail too; a bare
    note then stands in for it rather than escalating the failure.
    """
    try:
        return await summarize_messages(
            messages, model, summary_model_settings(llm_config)
        )

    except Exception:
        logger.warning("Subagent recovery summary failed", exc_info=True)

        return None


async def run_subagent(
    ctx: RunContext[UserDeps],
    task: str,
    *,
    capability: AbstractCapability[UserDeps],
) -> ToolReturn:
    """Run ``user_agent`` as a subagent composed from a single *capability*.

    The reusable core behind every subagent tool: drive a delegated run and
    surface its reasoning, tool calls, and messages to the frontend.  Drives the
    run with ``iter`` rather than ``run`` + ``capture_run_messages`` so its
    transcript comes off its own run (``run.all_messages()``), not an ambient
    capture context — the main chat run wraps the whole turn in
    ``capture_run_messages`` for crash-safe persistence, and a nested capture
    here would latch onto that outer list and summarise the wrong conversation.

    Each node's events stream live as ``data-subagent`` parts (via the sink on
    ``ctx.deps``), and the full transcript is packed onto the return metadata so
    it persists.  The run is bounded by ``settings.llm.subagent_timeout_seconds``
    and any failure short of a shared usage limit — a context overflow, the
    timeout, or an unexpected crash — is contained: the partial findings are
    summarised and returned instead of aborting the whole chat turn.
    """
    llm_config = _subagent_llm_config(ctx.deps)
    model = model_from_config(llm_config)
    tool_call_id = ctx.tool_call_id or ""
    builder = SubagentTranscriptBuilder(tool_call_id)
    sink = ctx.deps.subagent_sink

    def emit(update: SubagentUpdate | None) -> None:
        # The sink is unbounded, so `put_nowait` never blocks the run; it is
        # drained concurrently by the streaming response (None outside chat).
        if update is not None and sink is not None:
            sink.put_nowait(update)

    async with user_agent.iter(
        task,
        model=model,
        deps=ctx.deps,
        capabilities=[capability],
        usage=ctx.usage,
        usage_limits=turn_usage_limits,
    ) as run:
        # Pydantic AI exposes the run context with an ``Any`` output parameter,
        # while node streams retain the concrete output parameter.
        run_ctx: Any = run.ctx
        try:
            # `asyncio.timeout(None)` is a no-op, so a disabled timeout keeps the
            # plain iteration.  On expiry the surrounding block recovers the
            # partial findings, the same as any other contained failure.
            async with asyncio.timeout(settings.llm.subagent_timeout_seconds):
                async for node in run:
                    # Reasoning/message starts come off the model-request node,
                    # tool calls off the call-tools node; the builder
                    # discriminates both.
                    if not (
                        user_agent.is_model_request_node(node)
                        or user_agent.is_call_tools_node(node)
                    ):
                        continue

                    async with node.stream(run_ctx) as stream:
                        async for event in stream:
                            emit(builder.on_event(event))

        except UsageLimitExceeded:
            # A shared usage budget means the whole turn is out of requests, not
            # just this subagent — let it abort the turn rather than swallowing it.
            raise

        except Exception as exc:
            # A subagent that overflows, times out, or crashes has still done
            # useful work — its transcript is on the run.  Summarise the partial
            # findings and hand them back instead of failing the tool call, so
            # the main thread keeps them and continues.  Transcript fidelity
            # follows `settings.summarization`, the same config every
            # summarization consumer uses; the summary reuses the model that
            # just ran, so `_safe_summarize` degrades to a bare note if that
            # model is itself the cause of the failure.
            reason = _failure_reason(exc)
            logger.warning(
                "Subagent %s; recovering partial findings", reason, exc_info=exc
            )
            summary = await _safe_summarize(run.all_messages(), model, llm_config)
            findings = (
                f"Summary of the findings so far:\n\n{summary}"
                if summary is not None
                else "No partial summary could be produced."
            )
            return _subagent_result(
                tool_call_id,
                builder,
                f"The subagent {reason} before finishing. {findings}",
            )

        result = run.result  # a clean iteration always ends at a result
        if result is None:
            raise RuntimeError("subagent run produced no result")
        return _subagent_result(tool_call_id, builder, result.output)


@subagent_toolset.tool
async def explore(
    ctx: RunContext[UserDeps],
    task: ExploreTaskArg,
    scope: ExploreScopeArg = "documents",
) -> ToolReturn:
    """Explore using a subagent.

    Delegates to a subagent that can search and read within the chosen scope.
    Returns a summary of findings. Use this for broad exploration tasks
    like surveying available documents, finding patterns across files,
    reviewing past conversations, or researching topics on the web.
    """
    return await run_subagent(ctx, task, capability=SUBAGENT_CAPABILITIES[scope])
