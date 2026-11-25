"""What a chat turn stores and resumes, over a stored conversation.

Every stored message carries the id of the UI message it shows in
(:func:`stamp`, kept where pydantic-ai's adapters keep it), so the transcript
groups messages by it and a client addresses a turn by it
(:func:`kept_before`).  A turn's messages are measured against the replayed
history (:class:`Replay`), and a turn that ended without answering its calls
closes them with returns the model and the transcript read alike
(:func:`close_orphan_tool_calls`, :func:`abandon_pending_calls`).  The
decisions a user made and a run's error are recorded on the messages, so a
reload shows what the turn showed live.  The calls a paused run awaits are
kept on the conversation's record by its store.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Self

from exar.pydantic_ai.compaction import is_compaction
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
from pydantic_ai.ui._utils import (  # pyright: ignore[reportPrivateUsage]
    get_ui_message_id,
    set_ui_message_id,
)
from pydantic_ai.ui.vercel_ai.request_types import ToolApprovalResponded

from ..agents.tools.ask import DISMISSED_QUESTIONS
from ..db.conversations import CHAT_ERROR_KEY, is_hidden, is_user_request

__all__ = [
    "ABANDONED_APPROVAL_DENIAL",
    "IMPORTED_CALL_CLOSED",
    "INTERRUPTED_TOOL_ERROR",
    "RUN_ERROR_CLOSED",
    "Replay",
    "TurnDelta",
    "abandon_pending_calls",
    "close_orphan_tool_calls",
    "display_order",
    "kept_before",
    "record_approvals",
    "record_turn_error",
    "recorded_decisions",
    "stamp",
    "stored_turn",
    "ui_id",
]

logger = logging.getLogger(__name__)


# Content for the return that closes a tool call a stopped turn left running.
INTERRUPTED_TOOL_ERROR = "The tool call did not complete because the run ended early."


# Content for the return that closes a call a run error cut off, the words the
# event stream streams it with (a literal upstream rather than a constant).
RUN_ERROR_CLOSED = "Tool execution was interrupted by an error."


# Content for the return that closes a call an imported conversation still
# awaited, since its turn can no longer be resumed.
IMPORTED_CALL_CLOSED = (
    "The conversation was imported while this call still awaited an answer, "
    "so it was never executed."
)


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


def ui_id(message: ModelMessage) -> str | None:
    """The id of the UI message *message* shows in, as :func:`stamp` keeps it."""
    return get_ui_message_id(message)


def stamp(messages: Iterable[ModelMessage], ui_message_id: str) -> None:
    """Keep *ui_message_id* on each of *messages* as the UI message it shows in.

    It goes where pydantic-ai's UI adapters keep the id a message was loaded
    from, the reserved metadata namespace their ``dump_messages`` reads back
    and their server-held history matches a resubmitted message by, and which
    never reaches the client as metadata.  Mutates in place.
    """
    for message in messages:
        set_ui_message_id(message, ui_message_id)


def display_order(messages: Sequence[ModelMessage]) -> list[int]:
    """The positions of *messages* in the order the client streamed them.

    A compaction made for a prompt is stored ahead of it, as the history the
    prompt is sent after, while it streamed into the reply to that prompt, so
    it shows after the prompt.  It is told apart from a compaction of the
    reply before, which stays, by belonging to another UI message than that
    reply (:func:`stamp`).
    """
    order = list(range(len(messages)))

    for at in range(1, len(order) - 1):
        here, after = messages[order[at]], messages[order[at + 1]]

        if (
            is_compaction(here)
            and is_user_request(after)
            and ui_id(here) != ui_id(messages[order[at - 1]])
        ):
            order[at], order[at + 1] = order[at + 1], order[at]

    return order


def kept_before(
    messages: Sequence[ModelMessage],
    target: str | None,
    *,
    regenerate: bool,
    reply_id: str,
) -> list[ModelMessage] | None:
    """The history a turn that replaces the UI message *target* runs on, ``None`` when it replaces none.

    An edit replaces the user message *target*, a regenerate the reply
    *target*, the reply to the user message *target*, or, when the history
    holds no *target*, the reply to the last prompt.  Every message that shows
    before the replaced one is kept, in its stored order.

    The compaction made for the prompt the replaced turn answers summarizes
    only the history before that prompt, so it is kept too and shows in the
    new reply *reply_id*, on a copy, since stored messages never change.
    """
    order = display_order(messages)
    ids = [ui_id(messages[index]) for index in order]
    start = next((at for at, id in enumerate(ids) if id == target), None)

    if start is None and regenerate:
        start = next(
            (
                at + 1
                for at in reversed(range(len(order)))
                if is_user_request(messages[order[at]])
            ),
            None,
        )
    elif start is not None:
        replaces_prompt = is_user_request(messages[order[start]])

        # A message sent with a reply as its target continues after it.
        if not regenerate and not replaces_prompt:
            return None

        if regenerate and replaces_prompt:
            start = next(
                (at for at in range(start, len(ids)) if ids[at] != target), len(ids)
            )

    if start is None:
        return None

    shown_before = set(order[:start])
    prompt = order[start] if not regenerate else order[start - 1] if start else None
    carried = (
        prompt - 1
        if prompt
        and is_compaction(messages[prompt - 1])
        and prompt - 1 not in shown_before
        else None
    )
    kept: list[ModelMessage] = []

    for index, message in enumerate(messages):
        if index == carried:
            message = replace(message)
            stamp([message], reply_id)
        elif index not in shown_before:
            continue

        kept.append(message)

    return kept


@dataclass(frozen=True, slots=True)
class TurnDelta:
    """What a turn stores past the replayed history."""

    above: ModelMessage | None
    """The compaction a run put in place of the replayed tail, the regenerated prompt."""

    messages: Sequence[ModelMessage]
    """The messages the turn adds, after the history or, below a compaction, after it."""


@dataclass(frozen=True, slots=True)
class Replay:
    """The stored history a run replays, as the run normalizes it.

    A run starts from the history with consecutive requests merged
    (``repair_messages``), and takes the turn's prompt as its own request after
    it (:attr:`ChatAdapter.prompt`), so the turn's messages start at the
    normalized length.  Only the length is held, so a turn does not pin the
    history.
    """

    length: int
    ends_compacted: bool

    @classmethod
    def of(cls, history: Sequence[ModelMessage]) -> Self:
        """The replay of *history*."""
        replayed = repair_messages(history, repair_last_response=False)

        return cls(len(replayed), bool(replayed) and is_compaction(replayed[-1]))

    def turn(self, messages: Sequence[ModelMessage]) -> TurnDelta:
        """What the run of *messages* stores past the replay.

        A compaction ahead of a regenerated prompt takes the place of that
        stored prompt, and the turn's messages start with the prompt as the
        run sent it again after the compaction.
        """
        head = messages[self.length - 1] if self.length else None

        if head is not None and is_compaction(head) and not self.ends_compacted:
            return TurnDelta(head, messages[self.length :])

        return TurnDelta(None, messages[self.length :])


def stored_turn(
    turn: TurnDelta,
    kept: Sequence[ModelMessage],
    declined: ModelRequest | None,
    *,
    prompt_id: str | None,
    reply_id: str,
) -> list[ModelMessage]:
    """The history a turn saves: what it *kept*, the calls it *declined*, and *turn*.

    Each of the turn's messages is stamped with the UI message it shows in,
    the reply *reply_id*, its prompt the client's *prompt_id*.  A compaction
    that took the place of the regenerated prompt goes ahead of it, and the
    prompt as the run sent it again follows, under the prompt's id, on a new
    branch.  Mutates *turn*'s messages in place.
    """
    stamp(turn.messages, reply_id)

    if turn.above is not None:
        stamp([turn.above], reply_id)
        stamp(turn.messages[:1], ui_id(kept[-1]) or reply_id)
        kept = [*kept[:-1], turn.above]
    elif prompt_id is not None and (
        prompt := next((m for m in turn.messages if is_user_request(m)), None)
    ):
        stamp([prompt], prompt_id)

    return [*kept, *([declined] if declined else []), *turn.messages]


def _answered(messages: Iterable[ModelMessage]) -> set[str]:
    """The ids of the tool calls *messages* return a result for."""
    return {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }


def _record_decisions(
    message: ModelRequest, decisions: Mapping[str, ToolApprovalResponded]
) -> None:
    """Store *decisions* on the request carrying their calls' returns."""
    if decisions:
        message.metadata = {
            **(message.metadata or {}),
            _APPROVAL_DECISIONS_KEY: _DECISIONS.dump_python(
                dict(decisions), mode="json", by_alias=True, exclude_none=True
            ),
        }


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
    ``agents.output.with_data_chunk``).  Mutates in place.
    """
    for message in messages:
        if isinstance(message, ModelRequest):
            _record_decisions(
                message,
                {
                    part.tool_call_id: decisions[part.tool_call_id]
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                    and part.tool_call_id in decisions
                },
            )


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
    messages: list[ModelMessage],
    content: str,
    outcome: Literal["failed", "interrupted"],
) -> list[ModelMessage]:
    """Append returns closing any tool call left without a result.

    A tool that raises (or a run cut short) leaves a ``ToolCallPart`` with no
    matching return in ``capture_run_messages``, which would be a ``tool_use``
    without its ``tool_result`` on every later request.  Each orphan is closed
    with *content* and *outcome*, which the transcript shows as the stream did:
    a call a run error cut off as failed, one a stop cut off as stopped.

    Call this only when the run did not finish cleanly, since a clean finish may
    leave a genuine approval-pending call dangling, which must reload as an
    approval request.  Returns *messages* unchanged when nothing dangles.
    """
    resolved = _answered(messages)
    orphans = {
        part.tool_call_id: part
        for message in messages
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id not in resolved
    }

    if not orphans:
        return messages

    return [*messages, ModelRequest(parts=_closed(orphans.values(), content, outcome))]


def abandon_pending_calls(pending: DeferredToolRequests | None) -> ModelRequest | None:
    """Close the *pending* calls, or return ``None`` without any.

    A run that finishes awaiting approval or answers leaves its calls dangling
    on purpose, so the next request can carry the decisions and resume it.  A
    next request that carries a new prompt instead ends that run for good: the
    call can never be answered now, yet it stays in the stored history, where
    pydantic-ai repairs it on the way to the provider with a generic
    ``interrupted`` result.  The model reads that as a transient failure and
    reissues the identical call, which is left dangling in turn, so a single
    abandoned approval has every later turn repeat the same call.

    Answering it as a denial instead says what actually happened.  The denial
    is recorded like a user's (:func:`record_approvals`), with no note, since
    nobody typed one, and a question is closed as dismissed, just as if the
    user had dismissed it.  *pending* are the calls the conversation's record
    says its last run awaits.
    """
    if pending is None:
        return None

    declined = ModelRequest(
        parts=[
            *_closed(pending.approvals, ABANDONED_APPROVAL_DENIAL, "denied"),
            *_closed(pending.calls, DISMISSED_QUESTIONS, "failed"),
        ]
    )
    _record_decisions(
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
    calls: Iterable[ToolCallPart],
    content: str,
    outcome: Literal["denied", "failed", "interrupted"],
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
    :data:`CHAT_ERROR_KEY` on the metadata of the last message that shows (the
    assistant response, or the user prompt when the run failed before
    responding) lets the frontend re-render the banner from stored history.
    The key is UI-owned metadata, never sent back to the provider.  Mutates the
    target message in place.
    """
    target = next(
        (
            message
            for message in reversed(messages)
            if not is_hidden(message)
            and (
                (isinstance(message, ModelResponse) and message.parts)
                or is_user_request(message)
            )
        ),
        None,
    )

    if target is not None:
        target.metadata = {**(target.metadata or {}), CHAT_ERROR_KEY: error_text}
