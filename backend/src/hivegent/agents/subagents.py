"""The subagents of a chat turn, delegated to with pydantic-ai-harness.

While ``settings.subagents`` enables them, a turn's prefix carries the
`SubAgents` capability of pydantic-ai-harness over the subagents of
:data:`SUBAGENTS` (:func:`delegation`), with its `TaskControl`: the main agent
delegates a task in the background unless it waits for a short one
(:class:`BackgroundByDefault`), and the tasks outlive the turn under the
conversation's task owner
(:mod:`hivegent.delegations`), so a later turn steers, cancels, or resumes them.

A subagent runs on the turn's model with its reasoning and on its deps, and counts into the turn's usage,
so the turn's ``UsageLimits`` bound the main agent and all the subagents it
started together, however long they run.  It never asks the user: `DenyUnanswered` denies every call that
would wait for an approval, so the main agent makes that change itself.  It
is offered exactly the tools the policy of the turn's deps
(``UserDeps.policy``) allows, and a list withholds a subagent by its name or the delegation tools by theirs.  Its
history is compacted on overflow like a turn's (:mod:`.compaction`), a
crash reaches the main agent as a retry, and a model error as a result it
decides on.  After ``subagents.max_steps`` model requests, or ahead of the
harness's timeout, :class:`ReportDeadline` refuses its further calls and has
it report what it found, a subagent the timeout still stops passes on its
last text, and :class:`BoundedReport` bounds what it reports.
"""

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Self, override

from exar.pydantic_ai import DeclaredEffects, definition_action
from exar.tools import tool_action
from pydantic_ai import RunContext, SkipToolExecution
from pydantic_ai.capabilities import (
    AbstractCapability,
    Capability,
    RawToolArgs,
    ValidatedToolArgs,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.output import OutputContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness import DenyUnanswered
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents, TaskControl
from pydantic_core import from_json

from ..config import settings
from ..llm import thinking_model_settings
from ..prompts import (
    EXPLORE_INSTRUCTIONS,
    GROUNDING_INSTRUCTIONS,
    VERSION_INSTRUCTIONS,
    WRITE_INSTRUCTIONS,
)
from ..shared.l10n import Localized
from .app import default_model_settings, user_agent
from .common import AgentTools, RunPrefix, UserDeps, static_tools
from .compaction import compact_on_overflow
from .context import DOCUMENT_SCOPE
from .guards import spill_text
from .tools.conversation import conversation_toolset
from .tools.explore import explore_toolset
from .tools.web import web_enabled, web_toolset
from .tools.write import write_toolset

__all__ = [
    "DELEGATE_TOOL",
    "DELEGATION_TOOLS",
    "SUBAGENTS",
    "BackgroundByDefault",
    "BoundedReport",
    "ReportDeadline",
    "Subagent",
    "delegation",
]

DELEGATE_TOOL = "delegate_task"
"""The tool that delegates to a subagent, without which a turn delegates nothing."""

DELEGATION_TOOLS: Mapping[str, str] = {
    DELEGATE_TOOL: "Delegates a task to a subagent, in the background unless it waits for it.",
    "list_tasks": "Lists the delegated tasks of the turn and their states.",
    "check_task": "Shows what a running task is doing now, without waiting for it.",
    "message_task": "Steers a running task with a message.",
    "answer_task": "Answers a question a task asked.",
    "cancel_task": "Cancels a running task.",
}
"""The tools of `SubAgents` with its `TaskControl` and what each does.

They change nothing themselves, since every subagent is held to the host
policy, and a list withholds them by name like any tool.
"""


_BACKGROUND = "background"


def _background_schema() -> dict[str, Any]:
    return {
        "type": "boolean",
        "default": True,
        "description": (
            "Run the task in the background, the default, and get its report later, so you "
            "and the user go on meanwhile. Set false only for a short task whose result you "
            "need to go on, which moves to the background if it still runs after "
            f"{settings.subagents.auto_background_seconds:g} seconds."
        ),
    }


def _offers_background(tool_def: ToolDefinition) -> bool:
    """Whether *tool_def* is ``delegate_task`` taking ``background``, which the harness offers only with a task owner."""
    return (
        tool_def.name == DELEGATE_TOOL
        and _BACKGROUND in tool_def.parameters_json_schema.get("properties", {})
    )


def _background_by_default(tool_def: ToolDefinition) -> ToolDefinition:
    """*tool_def* of ``delegate_task`` with ``background`` defaulting to true."""
    schema = tool_def.parameters_json_schema
    properties: dict[str, Any] = {
        **schema["properties"],
        _BACKGROUND: _background_schema(),
    }

    return replace(
        tool_def, parameters_json_schema={**schema, "properties": properties}
    )


class BackgroundByDefault(AbstractCapability[UserDeps]):
    """Delegates a task in the background unless the main agent asks to wait for it.

    The harness's ``delegate_task`` waits by default, which blocks the turn
    and the user with it, so its ``background`` argument defaults to true in
    the schema the model reads, and a call that leaves it out runs in the
    background.  A task the main agent waits for moves to the background
    after ``subagents.auto_background_seconds`` (`TaskControl.auto_background_after`),
    so it never blocks the user for long, while a short one returns its
    report in the same reply rather than in a turn it wakes.
    """

    @override
    async def prepare_tools(
        self, ctx: RunContext[UserDeps], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        return [
            _background_by_default(tool_def)
            if _offers_background(tool_def)
            else tool_def
            for tool_def in tool_defs
        ]

    @override
    async def before_tool_validate(
        self,
        ctx: RunContext[UserDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
    ) -> RawToolArgs:
        if not _offers_background(tool_def):
            return args

        try:
            parsed = from_json(args or "{}") if isinstance(args, str) else args
        except ValueError:
            return args

        return {_BACKGROUND: True, **parsed} if isinstance(parsed, dict) else args


_REFUSAL = (
    "A subagent cannot ask the user for approval, so this call was not executed. "
    "Do not retry it, report the change to the agent that delegated to you instead, "
    "which makes it itself."
)


_REPORT_SHARE = 0.8
"""The share of ``settings.subagents.timeout_seconds`` after which a subagent reports what it found."""

_REPORT_NOW = (
    "Your budget is almost used up, so this call was not run and no further call will be. "
    "Report your findings now, with the paths and sources they rest on, and say "
    "what you did not get to."
)


@dataclass(kw_only=True)
class ReportDeadline(AbstractCapability[UserDeps]):
    """Has a subagent report what it found after *steps* model requests or *seconds* of its run, refusing its further calls.

    A subagent ends with its findings rather than running on, and rather than
    the harness's timeout cutting it off with a note and its last text, which
    may stop mid-task.  Every call after the deadline returns the instruction
    to report in place of its result, in English like every result, so its
    tools and instructions stay as they were and the cached prefix holds,
    which a final request without tools would break.  The steps count the
    subagent's own requests, while the turn's usage limits, which it shares,
    count those of the whole turn.

    Args:
        steps: How many model requests the run makes before it reports.
        seconds: How long the run works before it reports, ``None`` for no bound.
        clock: Reads the time *seconds* are counted on.
    """

    steps: int
    seconds: float | None = None
    clock: Callable[[], float] = time.monotonic
    due: float = field(default=math.inf, init=False)

    @override
    async def for_run(self, ctx: RunContext[UserDeps]) -> Self:
        run = replace(self)

        if self.seconds is not None:
            run.due = self.clock() + self.seconds

        return run

    @override
    async def before_tool_execute(
        self,
        ctx: RunContext[UserDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        if ctx.run_step >= self.steps or self.clock() >= self.due:
            raise SkipToolExecution(_REPORT_NOW)

        return args


class BoundedReport(AbstractCapability[UserDeps]):
    """Bounds what a subagent reports to ``llm.tool_output_max_chars``, saving the whole of a longer report.

    A background task reports to the main agent in a prompt of the harness
    rather than a tool return, which the output spill never sees, so the
    report is bounded where the subagent writes it, a foreground one alike.
    """

    @override
    async def after_output_process(
        self, ctx: RunContext[UserDeps], *, output_context: OutputContext, output: Any
    ) -> Any:
        if not isinstance(output, str):
            return output

        tmp = ctx.deps.tmp

        return await spill_text(
            None if tmp is None else tmp.path,
            output,
            settings.llm.tool_output_max_chars,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Subagent:
    """A subagent the main agent may delegate to by name.

    Args:
        description: What it does, for the main agent.
        tools: Its tools, held to the host policy of the turn's mode.
        instructions: Its instructions, rendered in the user's language like the main agent's.
        extras: Capabilities beside its tools.
        writes: Whether it is offered only in the ``write`` mode, whose writes ask nothing.
        clock: Reads the time its report deadline is counted on.
    """

    description: str
    tools: Sequence[AgentTools]
    instructions: Sequence[Localized[str]] = ()
    extras: Sequence[AbstractCapability[UserDeps]] = ()
    writes: bool = False
    clock: Callable[[], float] = time.monotonic

    def offered(self, name: str, deps: UserDeps) -> bool:
        """Whether a turn over *deps* offers it by *name*.

        Not while the policy of the deps denies its name or every one of its tools,
        and only in the ``write`` mode if it :attr:`writes`.
        """
        return (
            (not self.writes or deps.mode == "write")
            and deps.policy(tool_action(name, "read")) != "deny"
            and any(
                deps.policy(definition_action(tool_def)) != "deny"
                for tools in self.tools
                for tool_def in static_tools(tools)
            )
        )

    def delegate(
        self,
        name: str,
        prefix: RunPrefix,
        shared: Sequence[AbstractCapability[UserDeps]],
        level: int,
        subagents: Mapping[str, Subagent],
    ) -> SubAgent[UserDeps]:
        """The delegate for the turn of *prefix* at delegation *level*, run with the *shared* capabilities.

        It delegates on to *subagents* in turn.

        Its own capabilities and the shared ones are the prefix its history
        is compacted under, so the summary keeps the cached prefix.  It
        reports after ``subagents.max_steps`` requests or 80% of the timeout
        (:class:`ReportDeadline`), and its report is bounded (:class:`BoundedReport`).
        """
        timeout = settings.subagents.timeout_seconds
        own = [
            Capability(
                id=name,
                toolsets=[tools.toolset() for tools in self.tools],
                instructions=[part[prefix.deps.language] for part in self.instructions],
            ),
            *self.extras,
            *_delegation(prefix, level + 1, shared, subagents),
        ]
        deadline = ReportDeadline(
            steps=settings.subagents.max_steps,
            seconds=None if timeout is None else _REPORT_SHARE * timeout,
            clock=self.clock,
        )
        summary = replace(prefix, capabilities=[*own, *shared], instructions=None)

        return SubAgent(
            user_agent(
                name,
                str,
                [
                    *own,
                    deadline,
                    BoundedReport(),
                    compact_on_overflow(summary),
                ],
                model_settings=default_model_settings
                | thinking_model_settings(prefix.llm),
            ),
            description=self.description,
            timeout_seconds=timeout,
            on_model_error="return",
            can_ask_questions=False,
            keep_partial_output=True,
        )


_EXPLORE = Subagent(
    description=(
        "Explores the documents the user sees: searches, reads, and queries "
        "them, and reports its findings with their paths."
    ),
    tools=(explore_toolset,),
    instructions=(
        EXPLORE_INSTRUCTIONS,
        GROUNDING_INSTRUCTIONS,
        VERSION_INSTRUCTIONS,
    ),
    extras=(DOCUMENT_SCOPE,),
)
_WRITE = Subagent(
    description=(
        "Edits and writes the documents the user may change without being "
        "asked, and reports what it changed with the paths and what it left "
        "to you since it needs the user's approval."
    ),
    tools=(explore_toolset, write_toolset),
    instructions=(
        WRITE_INSTRUCTIONS,
        GROUNDING_INSTRUCTIONS,
        VERSION_INSTRUCTIONS,
    ),
    extras=(DOCUMENT_SCOPE,),
    writes=True,
)
_CONVERSATIONS = Subagent(
    description="Searches and reads the user's past conversations.",
    tools=(conversation_toolset,),
)
_WEB = Subagent(description="Researches a question on the web.", tools=(web_toolset,))

SUBAGENTS: Mapping[str, Subagent] = {
    "explore": _EXPLORE,
    "write": _WRITE,
    "conversations": _CONVERSATIONS,
    **({"web": _WEB} if web_enabled else {}),
}
"""The subagents by name, the web one only while web tools are configured."""


def _delegation(
    prefix: RunPrefix,
    level: int,
    shared: Sequence[AbstractCapability[UserDeps]],
    subagents: Mapping[str, Subagent],
) -> list[AbstractCapability[UserDeps]]:
    """The capabilities a run at delegation *level* delegates to *subagents* with.

    None beyond ``settings.subagents.max_depth``, while the deps withhold
    ``delegate_task``, or while they offer no subagent.
    """
    limits = settings.subagents
    deps = prefix.deps

    if (
        not limits.enabled
        or level > limits.max_depth
        or deps.policy(tool_action(DELEGATE_TOOL, "read")) == "deny"
    ):
        return []

    agents = [
        subagent.delegate(name, prefix, shared, level, subagents)
        for name, subagent in subagents.items()
        if subagent.offered(name, deps)
    ]

    if not agents:
        return []

    return [
        SubAgents(
            agents=agents,
            shared_capabilities=shared,
            contain_errors=True,
            max_depth=1 + limits.max_depth,
            task_control=TaskControl(
                auto_background_after=limits.auto_background_seconds,
                max_running_tasks=limits.max_running,
            ),
        ),
        BackgroundByDefault(),
        DeclaredEffects(effects=dict.fromkeys(DELEGATION_TOOLS, "read")),
    ]


def delegation(
    prefix: RunPrefix, subagents: Mapping[str, Subagent] = SUBAGENTS
) -> list[AbstractCapability[UserDeps]]:
    """The capabilities by which the main agent of a turn of *prefix* delegates to the *subagents*."""
    return _delegation(
        prefix, 1, (DenyUnanswered[UserDeps](message=_REFUSAL),), subagents
    )
