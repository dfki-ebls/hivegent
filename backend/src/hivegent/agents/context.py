"""Context notes: run state the model reads from the history, not the prompt.

The instructions precede the whole history, so anything rendered into them
that changes during a conversation (the mode, the document selection, the
user's memory, a nearly spent limit) voids the provider's cached prefix and
has the model reread the entire conversation.  Such state reaches the model as
the result of a ``get_context`` call instead: before each model request,
:class:`ContextNotes` renders every :class:`ContextSource` of the run and, for
each whose text differs from what the history last reported, injects a
synthetic call and its result through :meth:`~pydantic_ai.RunContext.enqueue`,
pydantic-ai's seam for injecting such an exchange.  The pair joins the run's
messages and is persisted with the turn, so a change only ever extends the
cached prefix, and an unchanged note is never repeated.

``get_context`` is a real read-only tool, so the history never shows a call
to a tool the model lacks, and the model may reread a kind on its own.  Its
schema names every kind whatever a run offers, which keeps it byte-identical
across modes and features.  Notes are written in the run's language like the
instructions, the tool's own results included, so a reread compares equal to
the note it repeats.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestContext

from ..db.memory import load_memory
from ..l10n import Language, Localized
from ..prompts import (
    INTERACTIVE_MODE_NOTE,
    LIMITS_CLEAR_NOTE,
    MEMORY_EMPTY_NOTE,
    NO_DOCUMENT_SCOPE_NOTE,
    READ_MODE_NOTE,
    WRITE_MODE_NOTE,
    request_limit_note,
    retry_limit_note,
)
from ..types import Mode
from .common import UserDeps

__all__ = [
    "CONTEXT_TOOL",
    "DOCUMENTS",
    "LIMITS",
    "MEMORY",
    "MODE",
    "ContextKind",
    "ContextNotes",
    "ContextSource",
]

CONTEXT_TOOL = "get_context"


class ContextKind(StrEnum):
    """What a context note reports."""

    MODE = "mode"
    DOCUMENTS = "documents"
    MEMORY = "memory"
    LIMITS = "limits"


ContextKindArg = Annotated[
    ContextKind,
    Field(
        description=(
            "`mode`: what you may change and whether changes wait for approval. "
            "`documents`: the documents the user selected or hid. "
            "`memory`: your memory as this conversation started. "
            "`limits`: whether a limit of this turn is close."
        )
    ),
]

type ContextRender = Callable[[RunContext[UserDeps]], Awaitable[str | None]]
"""Render a kind from the run, ``None`` for its default state."""


@dataclass(frozen=True, slots=True)
class ContextSource:
    """One kind of run state and how it is rendered.

    ``render`` returns ``None`` for the state that needs no note, which
    ``default`` words once an earlier note said otherwise, and a source without
    a default then says nothing.  A source that does not ``refresh`` is
    reported once per conversation, so the state it started with stays.
    """

    kind: ContextKind
    render: ContextRender
    default: Localized[str] | None = None
    refresh: bool = True

    def baseline(self, language: Language) -> str | None:
        """What a history that never reported this kind implies."""
        return None if self.default is None else self.default[language]

    async def text(self, ctx: RunContext[UserDeps]) -> str | None:
        """The note for the current state, ``None`` when there is nothing to say."""
        rendered = await self.render(ctx)

        return self.baseline(ctx.deps.language) if rendered is None else rendered


def _kind(call: ToolCallPart) -> ContextKind | None:
    """The kind a ``get_context`` call asked for, ``None`` for malformed arguments."""
    try:
        return ContextKind(call.args_as_dict().get("kind"))
    except ValueError:
        return None


def _reported_context(messages: Sequence[ModelMessage]) -> dict[ContextKind, str]:
    """The text the history last reported for each kind, injected or called."""
    calls: dict[str, ContextKind] = {}
    reported: dict[ContextKind, str] = {}

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_name == CONTEXT_TOOL:
                if (kind := _kind(part)) is not None:
                    calls[part.tool_call_id] = kind

            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                reported[calls[part.tool_call_id]] = part.model_response_str()

    return reported


@dataclass(slots=True)
class ContextNotes(AbstractCapability[UserDeps]):
    """Report each source's state as a ``get_context`` result whenever it changes.

    The notes are enqueued around a model request before its handler runs,
    whose ``before_model_request`` phase drains them into that very request.
    A run that must reproduce its history's prefix and nothing more, such as a
    summary, turns ``report`` off and keeps the tool.
    """

    sources: Sequence[ContextSource]
    report: bool = True
    # A fixed id, so the tool definition is the same object-free value each run.
    id: str | None = field(default="context", kw_only=True)
    _toolset: FunctionToolset[UserDeps] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._toolset = FunctionToolset[UserDeps]()
        self._toolset.add_function(self._get_context, name=CONTEXT_TOOL, takes_ctx=True)

    def get_toolset(self) -> FunctionToolset[UserDeps]:
        """The ``get_context`` tool every note poses as a call of."""
        return self._toolset

    async def _get_context(
        self, ctx: RunContext[UserDeps], kind: ContextKindArg
    ) -> str:
        """Report the current state of one kind of context.

        Each kind is reported on its own as a result of this tool whenever it
        changes, so the latest result of a kind is current, and a call is only
        needed to read one again.
        """
        source = next((s for s in self.sources if s.kind is kind), None)
        text = None if source is None else await source.text(ctx)

        return f"Nothing to report for `{kind}`." if text is None else text

    async def wrap_model_request(
        self,
        ctx: RunContext[UserDeps],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        """Enqueue a note for each source whose state the history does not hold."""
        if self.report:
            await self._enqueue(ctx)

        return await handler(request_context)

    async def _enqueue(self, ctx: RunContext[UserDeps]) -> None:
        reported = _reported_context(ctx.messages)
        calls: list[ToolCallPart] = []
        results: list[ToolReturnPart] = []

        for source in self.sources:
            last = reported.get(source.kind)

            if last is not None and not source.refresh:
                continue

            text = await source.text(ctx)
            known = source.baseline(ctx.deps.language) if last is None else last

            if text is None or text == known:
                continue

            call = ToolCallPart(CONTEXT_TOOL, {"kind": source.kind.value})
            calls.append(call)
            results.append(ToolReturnPart(CONTEXT_TOOL, text, call.tool_call_id))

        if calls:
            ctx.enqueue(ModelResponse(parts=calls), ModelRequest(parts=results))


# ─── Sources ───────────────────────────────────────────────────────────

_MODE_NOTES: Mapping[Mode, Localized[str]] = {
    "interactive": INTERACTIVE_MODE_NOTE,
    "write": WRITE_MODE_NOTE,
    "read": READ_MODE_NOTE,
}


async def _mode(ctx: RunContext[UserDeps]) -> str:
    return _MODE_NOTES[ctx.deps.mode][ctx.deps.language]


async def _documents(ctx: RunContext[UserDeps]) -> str | None:
    return await asyncio.to_thread(ctx.deps.describe_document_scope) or None


async def _memory(ctx: RunContext[UserDeps]) -> str:
    return await load_memory(ctx.deps.user) or MEMORY_EMPTY_NOTE[ctx.deps.language]


_LIMIT_THRESHOLD = 0.75
"""The share of a limit spent before the model is told to change course."""


def _failure_streaks(
    messages: Sequence[ModelMessage], run_id: str | None
) -> dict[str, int]:
    """Each tool's failures in a row within run *run_id*.

    Counted per request the way pydantic-ai counts ``ctx.retries``: a request
    with a retry for the tool extends its streak, and one with only a return
    for it ends the streak.
    """
    streaks: dict[str, int] = {}

    for message in messages:
        if not isinstance(message, ModelRequest) or message.run_id != run_id:
            continue

        failed = {
            part.tool_name
            for part in message.parts
            if isinstance(part, RetryPromptPart) and part.tool_name is not None
        }
        succeeded = {
            part.tool_name for part in message.parts if isinstance(part, ToolReturnPart)
        }

        for tool in succeeded - failed:
            streaks.pop(tool, None)

        for tool in failed:
            streaks[tool] = streaks.get(tool, 0) + 1

    return streaks


async def _limits(ctx: RunContext[UserDeps]) -> str | None:
    """Name each limit of the turn that is nearly spent.

    Two limits end a run: its request budget, the run's own ``usage_limits``
    counted on the usage the main agent shares with its subagents, and a
    tool's retries, ``ctx.max_retries`` failures in a row.  The request note
    names no count, so it is reported once as the threshold is crossed rather
    than on every request after it.
    """
    language = ctx.deps.language
    notes: list[str] = []
    limit = ctx.usage_limits.request_limit if ctx.usage_limits else None

    if limit is not None and ctx.usage.requests >= _LIMIT_THRESHOLD * limit:
        notes.append(request_limit_note(limit)[language])

    for tool, failures in sorted(_failure_streaks(ctx.messages, ctx.run_id).items()):
        if failures >= _LIMIT_THRESHOLD * ctx.max_retries:
            notes.append(retry_limit_note(tool, failures, ctx.max_retries)[language])

    return "\n".join(notes) or None


MODE = ContextSource(ContextKind.MODE, _mode)
DOCUMENTS = ContextSource(
    ContextKind.DOCUMENTS, _documents, default=NO_DOCUMENT_SCOPE_NOTE
)
MEMORY = ContextSource(ContextKind.MEMORY, _memory, refresh=False)
LIMITS = ContextSource(ContextKind.LIMITS, _limits, default=LIMITS_CLEAR_NOTE)
