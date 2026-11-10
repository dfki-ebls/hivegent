"""Vercel AI chat adapter for the server-owned conversation tree.

The adapter replays history from storage rather than from the browser echo, and
its event stream records UI-owned metadata and shows compactions live.

It also owns :func:`run_and_persist`, which streams a turn and mirrors the
run's message list to storage on every finish, and :func:`dump_messages_with_ids`,
which projects stored tree nodes to ``UIMessage``s whose ids are the node ids
the client addresses for edit / regenerate / branch-select.  What a turn stores
and resumes lives in :mod:`.turns`.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any, TypedDict, override
from uuid import UUID, uuid4

from pydantic_ai import AgentRunEvents, AgentRunResultEvent, capture_run_messages
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    CapabilityEvent,
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    ThinkingPart,
    ToolAvailabilityDeltaPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    DynamicToolApprovalRespondedPart,
    DynamicToolUIPart,
    ToolApprovalRequestedPart,
    ToolApprovalResponded,
    ToolApprovalRespondedPart,
    ToolInputAvailablePart,
    ToolOutputAvailablePart,
    ToolOutputErrorPart,
    ToolUIPart,
    UIMessage,
)
from pydantic_ai.ui.vercel_ai.response_types import (
    BaseChunk,
    DataChunk,
    ErrorChunk,
    MessageMetadataChunk,
)
from starlette.responses import StreamingResponse

from ..agents.approval import APPROVAL_NOTE_KEY
from ..agents.common import ASK_USER_TOOL
from ..agents.compaction import Compacted
from ..agents.subagent_events import SubagentUpdate
from ..agents.tools.ask import ANSWERS, DISMISSED_QUESTIONS
from ..db.conversations import prompt_items
from ..l10n import Localized
from .turns import (
    DEFERRED_KEY,
    DEFERRED_REQUESTS,
    IMPORTED_CALL_CLOSED,
    INTERRUPTED_TOOL_ERROR,
    close_orphan_tool_calls,
    pending_requests,
    record_approvals,
    record_turn_error,
    recorded_decisions,
)

__all__ = [
    "APPROVAL_METADATA_KEY",
    "SDK_VERSION",
    "SUBAGENT_CHUNK_TYPE",
    "BranchInfo",
    "ChatAdapter",
    "agent_events",
    "denial_message",
    "dump_messages_with_ids",
    "load_archived",
    "run_and_persist",
    "stream_response",
]

logger = logging.getLogger(__name__)

# Vercel AI data-stream protocol version the frontend speaks; request parsing
# and response dumping must agree on it. The frontend runs AI SDK v7 (`ai@7`,
# `@ai-sdk/react@4`), whose wire equals v6's, so this only reflects the client's
# real SDK major and reserves the value for future v7-only chunks.
SDK_VERSION = 7

# Transient data-part type carrying live subagent transcript snapshots; the
# frontend consumes these via `useChat({ onData })` (see `lib/chat/subagent.ts`).
SUBAGENT_CHUNK_TYPE = "data-subagent"

# Message metadata key carrying per-reasoning-block stream durations in
# milliseconds, keyed by run id and ordered by the reasoning parts of that run,
# so the runs of one turn merge into one object like any other metadata.
REASONING_DURATIONS_KEY = "reasoningDurationsMs"


_SAVE_FAILED = Localized(
    en="Failed to save the conversation.",
    de="Die Konversation konnte nicht gesichert werden.",
)

_RUN_FAILED = Localized(
    en="The response failed. Please try again.",
    de="Die Antwort ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
)


# Message metadata key mapping each approval-pending tool call id to the
# metadata its `ApprovalRequired` carried, set on the response that asked.
APPROVAL_METADATA_KEY = "approvalMetadata"


_ASK_USER_PART = f"tool-{ASK_USER_TOOL}"

type PersistTurn = Callable[[Sequence[ModelMessage]], Awaitable[None]]


async def agent_events[OutputT](
    run: AbstractAsyncContextManager[AgentRunEvents[OutputT]],
) -> AsyncIterator[NativeEvent]:
    """The events of *run*, an agent's ``run_stream_events``, for ``transform_stream``."""
    async with run as events:
        async for event in events:
            yield event


class BranchInfo(TypedDict):
    """Branch annotation merged into a forking node's ``UIMessage`` metadata.

    The camelCase keys are the wire shape the frontend reads; index and count
    are derived here from the sibling list the repository returns.
    """

    branchCount: int
    branchIndex: int
    siblingIds: list[str]


def denial_message(note: str | None) -> str:
    """What the model reads for a call the user denied, with their *note* if any.

    The note may say what to do instead, so it takes precedence over the
    standing instruction to stop and wait.

    >>> denial_message(None).startswith("The user rejected this tool call")
    True
    >>> "Use the archive folder." in denial_message("Use the archive folder.")
    True
    """
    if not note:
        return (
            "The user rejected this tool call, so it was not executed. Do not call "
            "the same tool again with the same or similar arguments. Stop working on "
            "this step, tell the user what you were about to do, and wait for their "
            "instructions."
        )

    return (
        "The user rejected this tool call, so it was not executed, and left this "
        f"note:\n\n{note}\n\nFollow the note if it says what to do instead. "
        "Otherwise do not call the same tool again with the same or similar "
        "arguments, tell the user what you were about to do, and wait for their "
        "instructions."
    )


@dataclass
class ChatEventStream[DepsT, OutputT](VercelAIEventStream[DepsT, OutputT]):
    """Event stream that records how long each thinking part took to stream
    and shows compactions as they happen.

    Vercel AI and Pydantic AI do not expose a first-class reasoning duration.
    We persist this as application message metadata instead of provider
    metadata, so it stays UI-owned and is not sent back to providers as part
    metadata on follow-up turns.

    The ``DeferredToolRequests`` a run ended on are stored on the response that
    holds the pending calls, which is what the next request resumes against
    (:func:`pending_requests`), and never sent to the client.  What each
    ``ApprovalRequired`` carried is stored beside them under
    :data:`APPROVAL_METADATA_KEY` for the client, live in the run's one metadata
    chunk and on reload alike, since the protocol's approval request chunk
    carries ids only.
    """

    _thinking_started_at: float | None = None
    _thinking_durations_ms: list[int] = field(default_factory=list[int])

    @override
    async def on_error(self, error: Exception) -> AsyncIterator[BaseChunk]:
        """Log the run error and stream a generic one, keeping internals server-side."""
        logger.error("Chat run failed", exc_info=error)

        async for chunk in super().on_error(error):
            yield (
                ErrorChunk(error_text=_RUN_FAILED.current)
                if isinstance(chunk, ErrorChunk)
                else chunk
            )

    async def handle_capability_event(
        self, event: CapabilityEvent
    ) -> AsyncIterator[BaseChunk]:
        """Show a compaction the run made, as the stored one reloads."""
        if isinstance(event, Compacted):
            async for chunk in self.handle_compaction(event.part):
                yield chunk

    async def handle_thinking_start(
        self, part: ThinkingPart, follows_thinking: bool = False
    ) -> AsyncIterator[BaseChunk]:
        self._thinking_started_at = time.monotonic()
        async for chunk in super().handle_thinking_start(
            part, follows_thinking=follows_thinking
        ):
            yield chunk

    async def handle_thinking_end(
        self, part: ThinkingPart, followed_by_thinking: bool = False
    ) -> AsyncIterator[BaseChunk]:
        if self._thinking_started_at is not None:
            self._thinking_durations_ms.append(
                round((time.monotonic() - self._thinking_started_at) * 1000)
            )
            self._thinking_started_at = None

        async for chunk in super().handle_thinking_end(
            part, followed_by_thinking=followed_by_thinking
        ):
            yield chunk

    async def handle_run_result(
        self, event: AgentRunResultEvent
    ) -> AsyncIterator[BaseChunk]:
        response = event.result.response
        ui_metadata: dict[str, Any] = {}

        if self._thinking_durations_ms:
            ui_metadata[REASONING_DURATIONS_KEY] = {
                event.result.run_id: self._thinking_durations_ms
            }

        if isinstance(output := event.result.output, DeferredToolRequests):
            ui_metadata[DEFERRED_KEY] = DEFERRED_REQUESTS.dump_python(
                output, mode="json"
            )

            if approvals := {
                call.tool_call_id: output.metadata[call.tool_call_id]
                for call in output.approvals
                if call.tool_call_id in output.metadata
            }:
                ui_metadata[APPROVAL_METADATA_KEY] = approvals

        if ui_metadata:
            response.metadata = {**(response.metadata or {}), **ui_metadata}

        async for chunk in super().handle_run_result(event):
            if isinstance(chunk, MessageMetadataChunk) and isinstance(
                chunk.message_metadata, dict
            ):
                chunk = chunk.model_copy(
                    update={
                        "message_metadata": _client_metadata(chunk.message_metadata)
                    }
                )

            yield chunk


def _client_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """*metadata* as the client sees it, without the stored requests."""
    return {key: value for key, value in metadata.items() if key != DEFERRED_KEY}


class ChatAdapter[DepsT, OutputT](VercelAIAdapter[DepsT, OutputT]):
    """``VercelAIAdapter`` wired to :class:`ChatEventStream`, client echo dropped."""

    def build_event_stream(self) -> ChatEventStream[DepsT, OutputT]:
        return ChatEventStream(
            self.run_input,
            accept=self.accept,
            sdk_version=self.sdk_version,
            server_message_id=self.server_message_id,
            manage_system_prompt=self.manage_system_prompt,
        )

    @cached_property
    @override
    def messages(self) -> list[ModelMessage]:
        """The client's own new prompt only — the server owns the rest.

        The base adapter appends the request's messages to the caller's
        ``message_history``, and the SDK's approval continuation re-sends the
        assistant message holding the pending call — already the last message
        of the replayed prefix, so appending it duplicates the ``tool_call_id``
        and loops the run (see ``backend/README.md``).  Approval decisions are
        unaffected: :attr:`approval_decisions` reads them from the request
        itself rather than from these messages.
        """
        return self.load_messages(
            [message for message in self.run_input.messages if message.role == "user"]
        )

    @cached_property
    def prompt(self) -> str | list[UserContent] | None:
        """The client's new prompt, as its one part holds it, ``None`` without one.

        A run takes it as its user prompt rather than appended to the history,
        since a run merges consecutive requests, which would merge the prompt
        into a stored request it follows: the returns closing an abandoned or
        errored turn, or a prompt whose run failed (see :class:`Replay`).
        """
        parts = [
            part
            for message in self.messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]

        if not parts:
            return None

        if len(parts) == 1:
            content = parts[0].content
            return content if isinstance(content, str) else list(content)

        return [item for part in parts for item in prompt_items(part)]

    @cached_property
    def approval_decisions(self) -> dict[str, ToolApprovalResponded]:
        """The user's answers to pending approvals, keyed by tool call id.

        Only ``approval-responded`` parts count: a denial the client already
        shows as ``output-denied`` was resolved by an earlier request.
        """
        return {
            part.tool_call_id: part.approval
            for message in self.run_input.messages
            if message.role == "assistant"
            for part in message.parts
            if isinstance(
                part, ToolApprovalRespondedPart | DynamicToolApprovalRespondedPart
            )
            and isinstance(part.approval, ToolApprovalResponded)
        }

    @cached_property
    def question_answers(self) -> dict[str, Any]:
        """The user's replies to ``ask_user`` calls, keyed by tool call id.

        An answer is the client's ``output-available`` part, validated since
        the client wrote it, and a dismissal its ``output-error`` part, whose
        text gives way to :data:`DISMISSED_QUESTIONS`.  The client re-sends
        every settled card of the message, so questions it answered by an
        earlier request are here too, and pydantic-ai ignores results for calls
        that are no longer pending.

        Raises:
            pydantic.ValidationError: If an answer does not fit its schema.
        """
        return {
            part.tool_call_id: ToolFailed(DISMISSED_QUESTIONS)
            if isinstance(part, ToolOutputErrorPart)
            else ANSWERS.dump_python(
                ANSWERS.validate_python(part.output), mode="json", exclude_none=True
            )
            for message in self.run_input.messages
            if message.role == "assistant"
            for part in message.parts
            if isinstance(part, ToolOutputAvailablePart | ToolOutputErrorPart)
            and part.type == _ASK_USER_PART
        }

    @cached_property
    @override
    def deferred_tool_results(self) -> DeferredToolResults | None:
        """Never derived from the request alone, :meth:`resume` builds them."""
        return None

    def resume(
        self, pending: DeferredToolRequests | None
    ) -> DeferredToolResults | None:
        """Resume the run with the user's decisions, notes, and answers to *pending*.

        *pending* are the stored requests (:func:`pending_requests`), so a
        decision or answer for a call that is no longer pending is dropped, as
        the client re-sends every settled card of the message.  The client's
        ``reason`` is the user's own note on either decision.  A denial wraps it
        into the refusal the model reads (:func:`denial_message`) and an
        approval hands it to the call as metadata, which
        :class:`~hivegent.agents.approval.ApprovalNotes` appends to its result.
        Answers to questions are the results of their calls as they stand.

        Raises:
            ValueError: If an answer does not fit its schema (a
                ``pydantic.ValidationError``), or a call is answered with the
                other kind of result, such as an approval answered like a question.
        """
        if pending is None:
            return None

        open_ids = {call.tool_call_id for call in (*pending.calls, *pending.approvals)}
        decisions = {
            call_id: decision
            for call_id, decision in self.approval_decisions.items()
            if call_id in open_ids
        }
        answers = {
            call_id: answer
            for call_id, answer in self.question_answers.items()
            if call_id in open_ids
        }

        if not decisions and not answers:
            return None

        return pending.build_results(
            calls=answers,
            approvals={
                call_id: True
                if decision.approved
                else ToolDenied(denial_message(decision.reason))
                for call_id, decision in decisions.items()
            },
            metadata={
                call_id: {APPROVAL_NOTE_KEY: decision.reason}
                for call_id, decision in decisions.items()
                if decision.approved and decision.reason
            },
        )


async def _persist_safely(
    persist: PersistTurn, messages: Sequence[ModelMessage]
) -> None:
    """Run *persist*, logging instead of raising on failure.

    It fires after the response has already streamed — and possibly during
    client-disconnect teardown — so a storage error must not crash the
    request; it is logged for follow-up instead.
    """
    try:
        await persist(messages)
    except Exception:
        logger.exception("Failed to persist conversation turn")


def _attach_approvals(
    messages: Iterable[UIMessage], decisions: Mapping[str, ToolApprovalResponded]
) -> None:
    """Put the recorded decision back on the part of every call it resolved.

    The counterpart of :func:`record_approvals`, so a reloaded card shows the
    decision and note it showed live, rather than the refusal text the
    projection copies from a denied return into ``reason``, the user's note.
    Every denial is recorded, an abandoned one by
    :func:`abandon_pending_calls`, so the recorded decision always wins.
    """
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolUIPart | DynamicToolUIPart) and (
                decision := decisions.get(part.tool_call_id)
            ):
                part.approval = decision


def _reopen_questions(
    messages: Sequence[UIMessage], pending: DeferredToolRequests | None
) -> None:
    """Show the *pending* questions as awaiting their answers, not an approval.

    The projection reads any dangling call as an approval request, since a call
    the run deferred looks the same in the history as one it gated, so the
    stored requests tell them apart.  Only the last message can hold one, as
    the turn folds into it (see :func:`_fold_turns`).
    """
    if not messages or pending is None:
        return

    questions = {call.tool_call_id for call in pending.calls}
    parts = messages[-1].parts

    for index, part in enumerate(parts):
        if (
            isinstance(part, ToolApprovalRequestedPart)
            and part.tool_call_id in questions
        ):
            parts[index] = ToolInputAvailablePart(
                type=part.type,
                tool_call_id=part.tool_call_id,
                input=part.input,
                call_provider_metadata=part.call_provider_metadata,
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


def _fold_turns(messages: Iterable[UIMessage]) -> list[UIMessage]:
    """Fold each assistant turn into one message, as the client streams it.

    The projection gives every model response a message of its own, while the
    client streams all responses of a turn into one, the continuation after an
    approval included.  A turn folds into its first message, whose id is the
    node that edit, regenerate, and branch navigation address, since only a
    turn's first response has siblings.
    """
    folded: list[UIMessage] = []

    for message in messages:
        if folded and folded[-1].role == message.role == "assistant":
            head = folded[-1]
            head.parts.extend(message.parts)
            head.metadata = _merge_metadata(head.metadata, message.metadata)
        else:
            folded.append(message)

    return folded


_HARNESS_PARTS = (RetryFeedbackPart, ToolAvailabilityDeltaPart)


def _shown(message: ModelMessage) -> ModelMessage:
    """*message* without what a transcript leaves out.

    Retry feedback and tool availability are addressed to the model, and the
    adapter would dump each as a message of its own between two responses of
    one turn, which then no longer folds into the message it streamed as.  The
    stored requests a response carries stay server side (:func:`_client_metadata`).
    """
    if isinstance(message, ModelResponse):
        return (
            replace(message, metadata=_client_metadata(message.metadata))
            if message.metadata and DEFERRED_KEY in message.metadata
            else message
        )

    parts = [part for part in message.parts if not isinstance(part, _HARNESS_PARTS)]

    return (
        message if len(parts) == len(message.parts) else replace(message, parts=parts)
    )


def dump_messages_with_ids(
    pairs: Sequence[tuple[UUID, ModelMessage]],
    *,
    siblings: Mapping[UUID, Sequence[UUID]] | None = None,
) -> list[UIMessage]:
    """Project ``(node_id, message)`` pairs to ``UIMessage``s keyed by node id.

    Each ``UIMessage.id`` is set to its head node's id, so the client's edit /
    regenerate / branch-select ``messageId`` maps straight back to a tree node.
    The adapter invokes the id generator with the exact objects passed here (it
    iterates this same list), so the identity lookup is an exact match and
    stays correct even when one request emits several ``UIMessage``s, one per
    run of same-role parts; a request left with only tool returns once the
    harness parts are dropped (see :func:`_shown`) emits none.  An
    unmapped object — unreachable today — degrades to a fresh non-addressable id
    rather than raising.  When given, *siblings* maps a forking node to its
    ordered sibling ids; its :class:`BranchInfo` is merged under a ``branch``
    metadata key (leaving the framework key intact) so the frontend can render
    branch navigation.  Calls a user decided are re-annotated with the decision
    and note the run itself does not record (see :func:`record_approvals`),
    pending questions are reopened rather than shown as approval requests, and
    each assistant turn is one message, as it streamed (see :func:`_fold_turns`).
    """
    pending = pending_requests([msg for _, msg in pairs])
    pairs = [(node_id, _shown(msg)) for node_id, msg in pairs]
    node_by_obj = {id(msg): node_id for node_id, msg in pairs}

    if siblings:
        for node_id, msg in pairs:
            sibs = siblings.get(node_id)
            if sibs is not None:
                sib_ids = list(sibs)
                branch = BranchInfo(
                    branchCount=len(sib_ids),
                    branchIndex=sib_ids.index(node_id),
                    siblingIds=[str(sibling) for sibling in sib_ids],
                )
                msg.metadata = {**(msg.metadata or {}), "branch": branch}

    def assign_id(msg: ModelMessage, _role: str, _index: int) -> str:
        return str(node_by_obj.get(id(msg)) or uuid4())

    ui_messages = _fold_turns(
        ChatAdapter.dump_messages(
            [msg for _, msg in pairs],
            generate_message_id=assign_id,
            sdk_version=SDK_VERSION,
        )
    )
    _attach_approvals(ui_messages, recorded_decisions(msg for _, msg in pairs))
    _reopen_questions(ui_messages, pending)

    return ui_messages


def load_archived(messages: Sequence[UIMessage]) -> list[ModelMessage]:
    """Load an exported transcript as the messages it stores, the inverse of :func:`dump_messages_with_ids`.

    A summary loads into the request after it, so it is split off as the
    response a compaction is stored as.  A call the transcript still awaited
    has no stored requests to resume, so it is closed.
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

        loaded.append(ModelResponse(parts=summaries))

        if rest := [p for p in message.parts if not isinstance(p, CompactionPart)]:
            loaded.append(replace(message, parts=rest))

    return close_orphan_tool_calls(loaded, IMPORTED_CALL_CLOSED)


async def _merge_subagent_events(
    stream: AsyncIterator[BaseChunk],
    sink: asyncio.Queue[SubagentUpdate],
) -> AsyncGenerator[BaseChunk]:
    """Interleave live subagent transcript snapshots into the protocol stream.

    A subagent runs inside a parent tool call, so its activity would otherwise be
    trapped until that tool returns.  A forwarder drains *sink* while a driver
    pumps the adapter *stream*; both feed one ordered queue, so subagent steps
    surface as they happen and stay in order with the parent's own chunks.

    Each snapshot becomes a transient ``data-subagent`` part (delivered to the
    client but never persisted): the authoritative transcript is persisted
    separately on the subagent tool's return metadata.  That persisted chunk is
    itself a parent ``stream`` chunk, so a trailing snapshot the forwarder has
    not drained when the run ends is simply superseded, never lost.
    """
    outbox: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def forward_sink() -> None:
        while True:
            outbox.put_nowait(("event", await sink.get()))

    async def drive() -> None:
        try:
            async for chunk in stream:
                outbox.put_nowait(("chunk", chunk))

        except BaseException as exc:  # noqa: BLE001
            # Cancellation included: the consumer below is the only place that
            # decides what a failed stream means, so everything is forwarded.
            outbox.put_nowait(("error", exc))

        else:
            outbox.put_nowait(("done", None))

    forwarder = asyncio.create_task(forward_sink())
    driver = asyncio.create_task(drive())
    try:
        while True:
            kind, payload = await outbox.get()
            if kind == "event":
                yield DataChunk(type=SUBAGENT_CHUNK_TYPE, data=payload, transient=True)
            elif kind == "chunk":
                yield payload
            elif kind == "error":
                raise payload
            else:  # done
                return

    finally:
        forwarder.cancel()
        driver.cancel()
        with contextlib.suppress(BaseException):
            await driver


def stream_response(chunks: AsyncIterator[BaseChunk]) -> StreamingResponse:
    """The UI message stream of *chunks*, encoded for the frontend's SDK version."""
    return VercelAIEventStream[None, str](sdk_version=SDK_VERSION).streaming_response(
        chunks
    )


def run_and_persist[DepsT, OutputT](
    adapter: ChatAdapter[DepsT, OutputT],
    stream: AsyncIterator[BaseChunk],
    *,
    persist: PersistTurn,
    subagent_sink: asyncio.Queue[SubagentUpdate] | None = None,
) -> AsyncIterator[BaseChunk]:
    """Stream a chat turn and persist the run's message list on every finish.

    *stream* is the still-unstarted chunk stream from ``adapter.run_stream``;
    iterating it inside ``capture_run_messages`` exposes the run's live message
    list, so whatever exists when the stream ends is persisted: the full turn on
    a clean finish, or the prompt plus completed messages on an error (the
    Vercel adapter turns run errors into an in-band error chunk, so the stream
    still ends normally).  An answer cut off mid-stream is captured by
    pydantic-ai upstream into ``capture_run_messages``, so this path persists
    whatever it holds.

    On a run error (streamed in-band as an ``ErrorChunk``), the turn is
    normalized before persisting so reload mirrors what streamed:
    :func:`close_orphan_tool_calls` closes the call the error aborted and
    :func:`record_turn_error` stores the error text for the reloaded banner.  A
    clean finish is persisted untouched, so a genuine approval-pending call stays
    dangling and reloads as an approval request rather than an error.

    When *subagent_sink* is given, live subagent transcript snapshots queued on
    it are interleaved into the response as transient ``data-subagent`` parts.

    Persistence is server-authoritative with no browser echo to recover from, so
    a failed write hard-fails the turn: on a clean drain the failure surfaces as
    a trailing error chunk; on a cancel the write is shielded and a failure can
    only be logged.

    Every write goes through :func:`record_approvals` first, so each decision the
    user made is stored with its note whichever way the turn ended.
    """

    async def persist_turn(messages: Sequence[ModelMessage]) -> None:
        record_approvals(messages, adapter.approval_decisions)
        await persist(messages)

    async def drain() -> AsyncIterator[BaseChunk]:
        if subagent_sink is None:
            async for chunk in stream:
                yield chunk
            return

        # `aclosing` guarantees the merge's driver/forwarder tasks (and the
        # underlying run) are torn down on client disconnect, not leaked.
        async with contextlib.aclosing(
            _merge_subagent_events(stream, subagent_sink)
        ) as merged:
            async for chunk in merged:
                yield chunk

    async def relay() -> AsyncIterator[BaseChunk]:
        error_text: str | None = None
        with capture_run_messages() as captured:
            try:
                async for chunk in drain():
                    if isinstance(chunk, ErrorChunk):
                        # A run error is streamed in-band, not raised; keep its
                        # text to normalize the errored turn below.  The chunk is
                        # the only surviving signal here: the event stream that
                        # saw the exception is built and discarded inside
                        # `adapter.run_stream`, out of this function's reach.
                        error_text = chunk.error_text
                    yield chunk
            except BaseException:
                # A cancel: close any in-flight call so it does not reload as
                # an approval, persist what completed (shielded, since nobody
                # waits for it, so a failure can only be logged) and propagate.
                await asyncio.shield(
                    _persist_safely(
                        persist_turn,
                        close_orphan_tool_calls(list(captured), INTERRUPTED_TOOL_ERROR),
                    )
                )
                raise

            messages = list(captured)
            if error_text is not None:
                messages = close_orphan_tool_calls(messages, error_text)
                record_turn_error(messages, error_text)

            try:
                await asyncio.shield(persist_turn(messages))
            except Exception:
                logger.exception("Failed to persist conversation turn")
                yield ErrorChunk(error_text=_SAVE_FAILED.current)

    return relay()
