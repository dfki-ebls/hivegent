"""Subagent-oriented agent tool registrations."""

from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ToolReturn
from pydantic_ai.models.openai import OpenAIChatModel

from ...config import settings
from ...llm import create_openai_chat_model, is_context_overflow
from ...prompts import EXPLORE_INSTRUCTIONS
from ...tools.base import ToolOutput
from ...tools.pydantic_ai import wrap_tool_output
from ..app import user_agent
from ..common import ExploreTaskArg, UserDeps
from ..subagent_events import SubagentTranscriptBuilder, SubagentUpdate
from ..summarize import summarize_messages
from .conversation import conversation_toolset
from .explore import explore_toolset
from .web import web_toolset

__all__ = [
    "explore",
    "explore_subagent_capability",
    "run_subagent",
    "subagent_toolset",
]

subagent_toolset: FunctionToolset[UserDeps] = FunctionToolset()

ExploreScopeArg = Annotated[
    Literal["documents", "conversations", "web"],
    Field(
        description=(
            "What to explore: `documents` for the document collection, "
            "`conversations` for past chat history, `web` for web research."
        ),
    ),
]

# A subagent (and the MCP exploration tool) *is* a document-exploration agent,
# so the explore toolset and its system-prompt persona ride together as one
# capability, distinct from the main agent's bare ``explore`` toolset bundle.
explore_subagent_capability: AbstractCapability[UserDeps] = Capability(
    id="explore-subagent",
    toolsets=[explore_toolset],
    instructions=EXPLORE_INSTRUCTIONS,
)

# Scope chosen by the ``explore`` tool; each is a self-contained capability the
# delegated run is composed from.
_SUBAGENT_SCOPES: dict[str, AbstractCapability[UserDeps]] = {
    "documents": explore_subagent_capability,
    "conversations": Capability(id="conversation", toolsets=[conversation_toolset]),
    "web": Capability(id="web", toolsets=[web_toolset]),
}


def _subagent_model(deps: UserDeps) -> OpenAIChatModel:
    """Build the model used for subagent calls.

    Subagents perform agentic exploration with large contexts and tool
    calling, so they reuse the main model rather than ``aux_model`` —
    tiny aux models tend to fail in these scenarios.
    """
    llm = deps.llm
    if llm:
        model = llm.model
        api_key = llm.api_key
        base_url = llm.base_url
        allow_private_base_url = llm.base_url_is_trusted
    else:
        model = settings.llm.model
        api_key = settings.llm.api_key
        base_url = settings.llm.base_url or None
        allow_private_base_url = bool(base_url)

    return create_openai_chat_model(
        model,
        api_key=api_key,
        base_url=base_url,
        allow_private_base_url=allow_private_base_url,
    )


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
    it persists.  A context-window overflow is summarised and returned instead
    of failing the call, so the parent keeps the partial findings.
    """
    model = _subagent_model(ctx.deps)
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
    ) as run:
        try:
            async for node in run:
                # Reasoning/message starts come off the model-request node, tool
                # calls off the call-tools node; the builder discriminates both.
                if user_agent.is_model_request_node(
                    node
                ) or user_agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            emit(builder.on_event(event))

        except Exception as exc:
            # A subagent that overflows its context window has still done
            # useful work — its transcript is on the run.  Compact it into a
            # summary instead of failing the tool call, so the main thread
            # keeps the findings and continues.  Transcript fidelity follows
            # `settings.summarization`, the same config every summarization
            # consumer uses; with tool parts included the summary request can
            # overflow again (it reuses the model that just overflowed on
            # those payloads), in which case the error surfaces like any
            # other tool error.
            if not is_context_overflow(exc):
                raise
            summary = await summarize_messages(run.all_messages(), model)
            return _subagent_result(
                tool_call_id,
                builder,
                "The subagent hit the model's context limit before "
                "finishing. Summary of the findings so far:\n\n"
                f"{summary}",
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
    return await run_subagent(ctx, task, capability=_SUBAGENT_SCOPES[scope])
