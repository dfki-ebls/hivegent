"""The one transcript of a stored history, as the client shows it.

:func:`dump_transcript` projects a stored history to the ``UIMessage``s the client
streamed, one per UI message id its messages carry.  That projection is the one
transcript: a reload reads it, and every turn ends on it (:func:`turn_snapshot`).
"""

from collections.abc import AsyncIterator, Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import assert_never
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    CapabilityLoadDeltaPart,
    CapabilityPart,
    CompactionPart,
    InstructionBaselinePart,
    InstructionDeltaPart,
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ModelSettingsDeltaPart,
    RetryFeedbackPart,
    RetryPromptPart,  # pyright: ignore[reportDeprecated]  # ty: ignore[deprecated]
    SpeechPart,
    SystemPromptPart,
    ToolAvailabilityDeltaPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.ui.vercel_ai import VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    DataUIPart,
    DynamicToolApprovalRequestedPart,
    DynamicToolInputAvailablePart,
    DynamicToolUIPart,
    StepStartUIPart,
    ToolApprovalRequestedPart,
    ToolApprovalResponded,
    ToolInputAvailablePart,
    ToolUIPart,
    UIMessage,
    UIMessagePart,
)
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, DataChunk
from starlette.responses import StreamingResponse

from ...agents.output import DATA_CHUNK_TYPE
from ...agents.transcripts import SUBAGENT_CHUNK_TYPE
from ...db.conversations import is_hidden, is_prompt, is_user_request
from ..turns import (
    IMPORTED_CALL_CLOSED,
    close_orphan_tool_calls,
    display_order,
    recorded_decisions,
    stamp,
    ui_id,
)
from .adapter import SDK_VERSION, ChatAdapter, _awaiting_approval

__all__ = [
    "CLIENT_DATA_TYPES",
    "TURN_CHUNK_TYPE",
    "UI_MESSAGES",
    "dump_transcript",
    "load_archived",
    "stream_response",
    "turn_snapshot",
]

# Transient data-part type carrying the stored version of the turn that just
# ended, which the client swaps in for what it streamed (see `turn_snapshot`).
TURN_CHUNK_TYPE = "data-turn"

# Data-part type of a compaction, which streams outside any step.
_COMPACTION_CHUNK_TYPE = "data-compaction"

# The data parts the client declares (`chatDataSchemas` in
# `frontend/src/lib/chat/message.ts`), kept in sync by hand.  Every other one
# stays server side (see `backend/README.md`).
CLIENT_DATA_TYPES = frozenset(
    {_COMPACTION_CHUNK_TYPE, SUBAGENT_CHUNK_TYPE, TURN_CHUNK_TYPE, DATA_CHUNK_TYPE}
)

UI_MESSAGES = TypeAdapter(list[UIMessage])
"""How the transcript goes on the wire, on reload and at the end of a turn alike."""


def _attach_approvals(
    messages: Iterable[UIMessage], decisions: Mapping[str, ToolApprovalResponded]
) -> None:
    """Put the recorded decision back on the part of every call it resolved.

    The counterpart of :func:`record_approvals`, so a reloaded card shows the
    decision and note it showed live, rather than the refusal text the
    projection copies from a denied return into ``reason``, the user's note.
    """
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolUIPart | DynamicToolUIPart) and (
                decision := decisions.get(part.tool_call_id)
            ):
                part.approval = decision


# What a call that streamed as available input carries.
_STREAMED_CALL_FIELDS: set[str] = {
    "type",
    "tool_name",
    "tool_call_id",
    "input",
    "call_provider_metadata",
}


def _reopen_calls(messages: Iterable[UIMessage], approvals: Collection[str]) -> None:
    """Show every unanswered call as it streamed, an approval request only for an approval.

    The projection reads any call without a return as an approval request,
    while the stream asks only for the *approvals* a run deferred.  A pending
    question streamed as available input, which the client shows as waiting.
    """
    for message in messages:
        for index, part in enumerate(message.parts):
            if (
                not isinstance(
                    part, ToolApprovalRequestedPart | DynamicToolApprovalRequestedPart
                )
                or part.tool_call_id in approvals
            ):
                continue

            available = (
                DynamicToolInputAvailablePart
                if isinstance(part, DynamicToolApprovalRequestedPart)
                else ToolInputAvailablePart
            )
            message.parts[index] = available.model_validate(
                part.model_dump(include=_STREAMED_CALL_FIELDS)
            )


def _merge_metadata(base: object, override: object) -> object:
    """Merge *override* into *base* as the AI SDK merges a message metadata chunk.

    Objects merge key by key, and anything else, a list included, is replaced.
    """
    if override is None:
        return base

    if not (isinstance(base, dict) and isinstance(override, dict)):
        return override

    return {
        **base,
        **{
            key: _merge_metadata(base.get(key), value)
            for key, value in override.items()
        },
    }


def _compaction_only(message: UIMessage) -> bool:
    """Whether *message* holds nothing but a compaction, which streams outside any step."""
    return all(part.type == _COMPACTION_CHUNK_TYPE for part in message.parts)


def _reaches_client(part: BaseChunk | UIMessagePart) -> bool:
    """Whether *part* is no data part or one in :data:`CLIENT_DATA_TYPES`."""
    return (
        not isinstance(part, DataChunk | DataUIPart) or part.type in CLIENT_DATA_TYPES
    )


def _client_parts(messages: Iterable[UIMessage]) -> Iterable[UIMessage]:
    """*messages* with only the parts that reach the client, dropping any left empty."""
    for message in messages:
        message.parts = [part for part in message.parts if _reaches_client(part)]

        if message.parts:
            yield message


def _is_shown(part: ModelRequestPart) -> bool:
    """Whether a transcript shows *part*, decided for every kind of request part.

    A subagent's report or question delivered as a prompt is addressed to the
    model, not typed by the user, so it shows only as the call it answers.
    Retry feedback, the deltas, and capability parts are run state addressed
    to the model, and the adapter would dump each as a message of its own
    between two responses of one turn, which then no longer folds into the
    message it streamed as.  The
    deltas dump as data parts :data:`CLIENT_DATA_TYPES` withholds too, and are
    listed so that a part kind a pydantic-ai update adds fails type checking
    until it is decided.
    """
    match part:
        case UserPromptPart():
            return is_prompt(part)

        case SystemPromptPart() | ToolReturnPart() | CompactionPart():
            return True

        case (
            RetryFeedbackPart()
            | RetryPromptPart()  # pyright: ignore[reportDeprecated]  # ty: ignore[deprecated]
            | ToolAvailabilityDeltaPart()
            | CapabilityLoadDeltaPart()
            | ModelSettingsDeltaPart()
            | InstructionDeltaPart()
            | InstructionBaselinePart()
            | CapabilityPart()
            | SpeechPart()
        ):
            return False

        case _:
            assert_never(part)


def _shown(message: ModelMessage) -> ModelMessage:
    """*message* without what a transcript leaves out (:func:`_is_shown`)."""
    if isinstance(message, ModelResponse):
        return message

    return replace(message, parts=[part for part in message.parts if _is_shown(part)])


def dump_transcript(
    messages: Sequence[ModelMessage], pending: DeferredToolRequests | None
) -> list[UIMessage]:
    """Project a stored history to the messages the client streamed.

    This is what a client sees on reload and what it swaps in once a turn
    ends (:func:`turn_snapshot`), so the stream and the store meet here.  The
    messages show in the order they streamed (:func:`.turns.display_order`),
    and those sharing a UI message id (:func:`.turns.stamp`) are one
    ``UIMessage`` under it, each model response opening a step as it streams.
    Hidden messages are left out, decided calls carry the decision and note
    the run does not record (:func:`.turns.record_approvals`), and unanswered
    calls show as they streamed, an approval request only for one of the
    *pending* calls the conversation's last run awaits an approval for
    (:func:`_reopen_calls`).
    """
    shown = [
        _shown(messages[index])
        for index in display_order(messages)
        if not is_hidden(messages[index])
    ]
    transcript: list[UIMessage] = []

    for message in _client_parts(
        ChatAdapter.dump_messages(shown, sdk_version=SDK_VERSION)
    ):
        if message.role == "system":
            continue

        parts = (
            [StepStartUIPart(), *message.parts]
            if message.role == "assistant" and not _compaction_only(message)
            else message.parts
        )

        if transcript and transcript[-1].id == message.id:
            last = transcript[-1]
            last.parts.extend(parts)
            last.metadata = _merge_metadata(last.metadata, message.metadata)
        else:
            transcript.append(message.model_copy(update={"parts": list(parts)}))

    _attach_approvals(transcript, recorded_decisions(messages))
    _reopen_calls(transcript, _awaiting_approval(pending))

    return transcript


def load_archived(messages: Sequence[UIMessage]) -> list[ModelMessage]:
    """Load an exported transcript as the messages it stores, the inverse of :func:`dump_transcript`.

    Each message keeps the UI message id it was loaded from, and the steps of a
    reply after its first, which load without one, take the reply's.  A summary loads
    into the request after it, so it is split off as the response a compaction
    is stored as.  A call the transcript still awaited has no paused run to
    resume, so it is closed.
    """
    loaded: list[ModelMessage] = []

    for message in ChatAdapter.load_messages(messages):
        summaries = (
            [part for part in message.parts if isinstance(part, CompactionPart)]
            if isinstance(message, ModelRequest)
            else []
        )

        if not summaries:
            loaded.append(message)
            continue

        compaction = ModelResponse(parts=summaries)

        if (loaded_id := ui_id(message)) is not None:
            stamp([compaction], loaded_id)

        loaded.append(compaction)

        if rest := [p for p in message.parts if not isinstance(p, CompactionPart)]:
            loaded.append(replace(message, parts=rest))

    closed = close_orphan_tool_calls(loaded, IMPORTED_CALL_CLOSED, "failed")
    reply: str | None = None

    for message in closed:
        if is_user_request(message):
            reply = None
        elif (loaded_id := ui_id(message)) is not None:
            reply = loaded_id
        else:
            reply = reply or str(uuid4())
            stamp([message], reply)

    return closed


def turn_snapshot(
    messages: Sequence[ModelMessage],
    pending: DeferredToolRequests | None,
    since: str | None,
) -> DataChunk:
    """The stored version of a turn, the transcript of *messages* from the UI message *since* on.

    The client swaps it in for what it streamed once the turn ends, so the
    transcript it keeps is the one a reload shows, ids included.  The turn
    starts at the first UI message it stored a message under, which is the
    reply before its prompt when it closed the calls that reply awaited.
    Only the messages from there on are projected, in the order they show,
    since a UI message holds every call with its return.  Without *since*
    the whole history is.
    """
    shown = [messages[index] for index in display_order(messages)]
    start = next(
        (at for at, message in enumerate(shown) if since and ui_id(message) == since),
        0,
    )
    turn = dump_transcript(shown[start:], pending)

    return DataChunk(
        type=TURN_CHUNK_TYPE,
        data=UI_MESSAGES.dump_python(turn, mode="json", by_alias=True),
        transient=True,
    )


def stream_response(chunks: AsyncIterator[BaseChunk]) -> StreamingResponse:
    """The UI message stream of *chunks*, encoded for the frontend's SDK version.

    Only the parts that reach the client are sent (:data:`CLIENT_DATA_TYPES`).
    """
    return VercelAIEventStream[None, str](sdk_version=SDK_VERSION).streaming_response(
        chunk async for chunk in chunks if _reaches_client(chunk)
    )
