"""What a chat turn stores and resumes, over the server-owned message tree.

A turn's messages are measured against the replayed history (:class:`Replay`),
the deferred calls a paused turn awaits are stored on its response
(:func:`pending_requests`), and a turn that ended without answering its calls
closes them (:func:`close_orphan_tool_calls`, :func:`abandon_pending_calls`).
The decisions a user made and a run's error are recorded on the messages, so a
reload shows what the turn showed live.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    repair_messages,
)
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.ui.vercel_ai.request_types import ToolApprovalResponded

from ..agents.compaction import is_compaction
from ..agents.tools.ask import DISMISSED_QUESTIONS
from ..db.conversations import CHAT_ERROR_KEY, is_user_request

__all__ = [
    "ABANDONED_APPROVAL_DENIAL",
    "DEFERRED_KEY",
    "DEFERRED_REQUESTS",
    "INTERRUPTED_TOOL_ERROR",
    "Replay",
    "abandon_pending_calls",
    "close_orphan_tool_calls",
    "pending_requests",
    "record_approvals",
    "record_turn_error",
    "recorded_decisions",
]

logger = logging.getLogger(__name__)


# Content for the synthetic failed return that closes a tool call left dangling
# by an interrupted run (client disconnect) when no stream error text is known.
INTERRUPTED_TOOL_ERROR = "The tool call did not complete because the run ended early."


# Content for the denial that closes an approval request the user walked away
# from by sending another message instead of answering it.
ABANDONED_APPROVAL_DENIAL = (
    "The user did not answer this approval request and moved on to another "
    "message, so the call was never executed. Do not reissue it unless they "
    "ask for it again."
)


# Message metadata key mapping each tool call a user decision resolved to that
# decision, set on the request that carries their returns (see `record_approvals`).
_APPROVAL_DECISIONS_KEY = "approvalDecisions"


_DECISIONS = TypeAdapter(dict[str, ToolApprovalResponded])


# Server-only message metadata key holding the `DeferredToolRequests` a run
# ended on, set on its response and never sent to the client.
DEFERRED_KEY = "deferredToolRequests"


DEFERRED_REQUESTS = TypeAdapter(DeferredToolRequests)


@dataclass(frozen=True, slots=True)
class Replay:
    """The stored history a run replays, as the run normalizes it.

    A run starts from the history with consecutive requests merged
    (``repair_messages``), and takes the turn's prompt as its own request after
    it (:attr:`ChatAdapter.prompt`), so the turn's messages start at the
    normalized length.  Only the length is held, so a turn does not pin the
    prefix.
    """

    length: int
    ends_compacted: bool

    @classmethod
    def of(cls, prefix: Sequence[ModelMessage]) -> Self:
        """The replay of *prefix*."""
        replayed = repair_messages(prefix, repair_last_response=False)

        return cls(len(replayed), bool(replayed) and is_compaction(replayed[-1]))

    def turn(
        self, messages: Sequence[ModelMessage]
    ) -> tuple[ModelMessage | None, Sequence[ModelMessage]]:
        """The compaction a run put in place of the replayed tail, and the messages it added.

        A compaction ahead of a regenerated prompt takes the place of that
        stored prompt, the fork node, so it goes above it, and the prompt the
        run sends again after it is stored already.
        """
        head = messages[self.length - 1] if self.length else None

        if head is not None and is_compaction(head) and not self.ends_compacted:
            return head, messages[self.length + 1 :]

        return None, messages[self.length :]


def _answered(messages: Iterable[ModelMessage]) -> set[str]:
    """The ids of the tool calls *messages* return a result for."""
    return {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }


def pending_requests(messages: Sequence[ModelMessage]) -> DeferredToolRequests | None:
    """The deferred calls the last response of *messages* still awaits, or ``None``.

    A run that pauses stores its ``DeferredToolRequests`` on that response
    (:class:`ChatEventStream`), which need not be the last message: the calls it
    ran alongside the deferred ones answer in a request after it.  A call
    answered after it, as an abandoned one is, is no longer pending.
    """
    index = next(
        (
            index
            for index in reversed(range(len(messages)))
            if isinstance(messages[index], ModelResponse)
        ),
        None,
    )

    if index is None or (
        stored := (messages[index].metadata or {}).get(DEFERRED_KEY)
    ) is None:
        return None

    requests = DEFERRED_REQUESTS.validate_python(stored)
    answered = _answered(messages[index + 1 :])
    pending = DeferredToolRequests(
        calls=[call for call in requests.calls if call.tool_call_id not in answered],
        approvals=[
            call for call in requests.approvals if call.tool_call_id not in answered
        ],
        metadata=requests.metadata,
    )

    return pending if pending.calls or pending.approvals else None


def record_approvals(
    messages: Sequence[ModelMessage], decisions: Mapping[str, ToolApprovalResponded]
) -> None:
    """Store this turn's approval decisions and the user's notes, for reload.

    An approved call simply runs and stores an ordinary successful return, so
    nothing in the message list says it was ever gated, and a denial stores the
    refusal the model reads rather than the note the user typed.  Either way
    the decision on screen live would not survive a reload.

    Each decision is recorded on the request carrying its call's return, which
    is UI-owned metadata that never reaches the provider, like the reasoning
    durations and the turn error beside it.  The return part's own ``metadata``
    would be the closer home but is already the tool-output chunk channel (see
    ``tools.pydantic_ai.wrap_tool_output``).
    :func:`dump_messages_with_ids` reads it back.  Mutates in place.
    """
    if not decisions:
        return

    for message in messages:
        if not isinstance(message, ModelRequest):
            continue

        _stamp(
            message,
            {
                part.tool_call_id: decisions[part.tool_call_id]
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id in decisions
            },
        )


def _stamp(
    message: ModelRequest, decisions: Mapping[str, ToolApprovalResponded]
) -> None:
    """Store *decisions* on the request carrying their calls' returns, the one form every decision takes."""
    if decisions:
        message.metadata = {
            **(message.metadata or {}),
            _APPROVAL_DECISIONS_KEY: _DECISIONS.dump_python(
                dict(decisions), mode="json", by_alias=True, exclude_none=True
            ),
        }


def recorded_decisions(
    messages: Iterable[ModelMessage],
) -> dict[str, ToolApprovalResponded]:
    """The decisions :func:`record_approvals` stored on *messages*."""
    decisions: dict[str, ToolApprovalResponded] = {}

    for message in messages:
        recorded = (message.metadata or {}).get(_APPROVAL_DECISIONS_KEY)

        if recorded is None:
            continue

        try:
            decisions.update(_DECISIONS.validate_python(recorded))
        except ValidationError:
            logger.warning("Ignoring malformed approval decisions %r", recorded)

    return decisions


def close_orphan_tool_calls(
    messages: list[ModelMessage], content: str
) -> list[ModelMessage]:
    """Append synthetic failed returns for any tool call left without a result.

    A tool that raises (or a run cut short) leaves a ``ToolCallPart`` with no
    matching return in ``capture_run_messages``.  Persisted verbatim, the reload
    projection reads that dangling call as an approval request and the history
    becomes invalid to replay to the provider (a ``tool_use`` with no
    ``tool_result``).  Closing each orphan with a ``ToolReturnPart``
    (``outcome='failed'``) carrying *content* keeps the stored turn a faithful,
    replayable mirror of the streamed one: the tool card reloads as an error and
    the next turn sees a well-formed tool result.

    Call this only when the run did not finish cleanly — a clean finish may leave
    a genuine approval-pending call dangling, which must reload as an approval
    request, not an error.  Returns *messages* unchanged when nothing dangles.
    """
    resolved = _answered(messages)
    orphans = {
        part.tool_call_id: part.tool_name
        for message in messages
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id not in resolved
    }

    if not orphans:
        return messages

    returns = [
        ToolReturnPart(
            tool_name=tool_name,
            content=content,
            tool_call_id=tool_call_id,
            outcome="failed",
        )
        for tool_call_id, tool_name in orphans.items()
    ]

    return [*messages, ModelRequest(parts=returns)]


def abandon_pending_calls(pending: DeferredToolRequests | None) -> ModelRequest | None:
    """Close the *pending* calls, or return ``None`` without any.

    A run that finishes awaiting approval or answers leaves its calls dangling
    on purpose, so the next request can carry the decisions and resume it.  A
    next request that carries a new prompt instead ends that run for good: the
    call can never
    be answered now, yet it stays in the stored history, where pydantic-ai
    repairs it on the way to the provider with a generic ``interrupted``
    result.  The model reads that as a transient failure and reissues the
    identical call, which is left dangling in turn — so a single abandoned
    approval has every later turn repeat the same call with the same arguments.

    Answering it as a denial instead says what actually happened, and once the
    refusal is stored the history holds a resolved call rather than one that is
    repaired again on every future turn.  The denial is recorded like a user's
    (:func:`record_approvals`), with no note, since nobody typed one.  A
    question is closed as dismissed, just as if the user had dismissed it.

    *pending* are the stored requests (:func:`pending_requests`), which say
    which calls await an approval and which an answer.  An earlier dangling
    call needs no handling here, it was already closed when its own turn ended.
    """
    if pending is None:
        return None

    declined = ModelRequest(
        parts=[
            *_closed(pending.approvals, ABANDONED_APPROVAL_DENIAL, "denied"),
            *_closed(pending.calls, DISMISSED_QUESTIONS, "failed"),
        ]
    )
    _stamp(
        declined,
        {
            call.tool_call_id: ToolApprovalResponded(
                id=call.tool_call_id, approved=False
            )
            for call in pending.approvals
        },
    )

    return declined


def _closed(
    calls: Iterable[ToolCallPart], content: str, outcome: Literal["denied", "failed"]
) -> list[ToolReturnPart]:
    """The returns that close *calls* with *content* and *outcome*."""
    return [
        ToolReturnPart(
            tool_name=call.tool_name,
            content=content,
            tool_call_id=call.tool_call_id,
            outcome=outcome,
        )
        for call in calls
    ]


def record_turn_error(messages: list[ModelMessage], error_text: str) -> None:
    """Store *error_text* on the turn's last visible message for reload.

    A run error is shown live as a chat-level banner but is a stream event, not
    a message part, so it is lost on reload.  Recording it under
    :data:`CHAT_ERROR_KEY` on the metadata of the last message that projects to a
    ``UIMessage`` (the assistant response, or the user prompt when the run failed
    before responding) lets the frontend re-render the banner from stored
    history.  The key is UI-owned metadata, never sent back to the provider (like
    the reasoning durations alongside it).  Mutates the target message in place.
    """
    target = next(
        (
            message
            for message in reversed(messages)
            if (isinstance(message, ModelResponse) and message.parts)
            or is_user_request(message)
        ),
        None,
    )

    if target is not None:
        target.metadata = {**(target.metadata or {}), CHAT_ERROR_KEY: error_text}
