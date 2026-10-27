"""Compaction in place: a summary stored in the conversation it compacts.

A compaction is a ``ModelResponse`` holding one provider-less
``CompactionPart`` whose content is the summary (see "Compaction is a turn of
the conversation" in ``backend/README.md``).  It goes where the history before
it can be closed off (:func:`~.summarize.closed_cuts`), and only past the
latest compaction with something new to summarize.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import ModelRequestNode, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    CapabilityEvent,
    CompactionPart,
    InstructionBaselinePart,
    InstructionDeltaPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    ToolReturnPart,
    post_compaction_window,
)
from pydantic_graph import End

from ..db.conversations import is_user_request
from ..llm import is_context_overflow
from .common import RunPrefix, UserDeps
from .summarize import closed_cuts, summarize_conversation

__all__ = [
    "CompactOnOverflow",
    "Compacted",
    "compact",
    "compaction_cut",
    "is_compaction",
]


@dataclass(kw_only=True)
class Compacted(CapabilityEvent, namespace="hivegent"):
    """A run compacted its history, so the client shows the summary live."""

    part: CompactionPart


async def compact(messages: Sequence[ModelMessage], run: RunPrefix) -> CompactionPart:
    """The compaction that summarizes *messages*, run under *run*."""
    return CompactionPart(content=await summarize_conversation(messages, run))


def is_compaction(message: ModelMessage) -> bool:
    """Whether *message* is a compaction."""
    return isinstance(message, ModelResponse) and any(
        isinstance(part, CompactionPart) for part in message.parts
    )


def _is_prompt(message: ModelMessage) -> bool:
    """Whether *message* opens a turn, a prompt that answers no call."""
    return is_user_request(message) and not any(
        isinstance(part, ToolReturnPart | RetryFeedbackPart) for part in message.parts
    )


def _boundaries(messages: Sequence[ModelMessage]) -> list[int]:
    """Where a compaction can go in *messages*, ascending.

    A cut right after the latest compaction is left out, since there is
    nothing new to summarize, so a window that overflows on its own is not
    compacted again.  A cut past the latest prompt is left out until a model
    answered it, so a prompt is never summarized with only what a capability
    reported beside it, such as :class:`~.tools.memory.MemoryNote`'s read.
    """
    start = len(messages) - len(post_compaction_window(messages))
    prompt = max(
        (i for i, message in enumerate(messages) if _is_prompt(message)), default=-1
    )
    answered = next(
        (
            i
            for i, message in enumerate(messages)
            if i > prompt and isinstance(message, ModelResponse) and message.model_name
        ),
        len(messages),
    )

    return [
        cut
        for cut in closed_cuts(messages)
        if cut > start + 1 and (cut <= prompt or cut > answered)
    ]


def compaction_cut(messages: Sequence[ModelMessage]) -> int | None:
    """Where a compaction of *messages* goes, ``None`` where none can.

    It goes after them where that closes the history, so it summarizes what the
    client has already seen.  A trailing prompt is never summarized, so the
    compaction goes ahead of it and the prompt follows the summary.

    Examples:
        >>> from pydantic_ai.messages import TextPart, UserPromptPart
        >>> ask = ModelRequest(parts=[UserPromptPart("hi")])
        >>> answer = ModelResponse(parts=[TextPart("hello")], model_name="m")
        >>> compaction_cut([ask, answer]), compaction_cut([ask, answer, ask])
        (2, 2)
        >>> compaction_cut([ask]) is None
        True
    """
    boundaries = _boundaries(messages)

    if len(messages) in boundaries:
        return len(messages)

    if (
        messages
        and isinstance(messages[-1], ModelRequest)
        and len(messages) - 1 in boundaries
    ):
        return len(messages) - 1

    return None


def _retry(request: ModelRequest) -> ModelRequest:
    """*request* without the instruction state its refused attempt recorded.

    A compaction opens a new instruction window, which the retry records afresh.
    """
    return replace(
        request,
        parts=[
            part
            for part in request.parts
            if not isinstance(part, InstructionDeltaPart | InstructionBaselinePart)
        ],
    )


@dataclass
class CompactOnOverflow(AbstractCapability[UserDeps]):
    """Compact a history the context window refused, then retry the request.

    The compaction goes at :func:`compaction_cut`.  After a refused tool
    return, the retry is an empty request that continues from the summary.
    Ahead of a refused prompt, the retry sends that prompt again.

    A regenerated prompt is persisted already, so the turn stores a
    compaction that took its place ahead of it.
    """

    run: RunPrefix

    async def on_node_run_error(
        self, ctx: RunContext[UserDeps], *, node: Any, error: Exception
    ) -> ModelRequestNode[UserDeps, Any] | End[Any]:
        """Compact and retry an overflowing model request, re-raise anything else."""
        if not isinstance(node, ModelRequestNode) or not is_context_overflow(error):
            raise error

        history = list(ctx.messages)

        # A stream refused on opening leaves an empty response behind.
        if history and isinstance(history[-1], ModelResponse) and not history[-1].parts:
            history.pop()

        cut = compaction_cut(history)

        if cut is None or not isinstance(refused := history[-1], ModelRequest):
            raise error

        part = await compact(history[:cut], self.run)
        compaction = ModelResponse(parts=[part])

        if cut < len(history):
            # The retry appends the refused request again.
            history[-1] = compaction
            retry = _retry(refused)

        else:
            history.append(compaction)
            retry = ModelRequest(parts=[])

        ctx.messages[:] = history
        await ctx.emit(Compacted(part=part))

        return replace(node, request=retry)
