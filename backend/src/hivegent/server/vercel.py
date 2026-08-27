"""Vercel AI chat adapter that emits canonical error codes.

The Vercel AI data stream protocol carries stream errors as a bare
``errorText`` string, so the frontend cannot see HTTP status codes or
finish reasons.
This module classifies errors on the server, where the exception type,
status code, and provider response body are still structured, and
prefixes context-window overflows with a stable code that the frontend
matches exactly (see ``isContextLengthError`` in
``frontend/src/lib/chat/chat-utils.ts``).

It also owns :func:`run_and_persist`, which streams a turn and mirrors the
run's message list to storage on every finish, and :func:`dump_messages_with_ids`,
which projects stored tree nodes to ``UIMessage``s whose ids are the node ids
the client addresses for edit / regenerate / branch-select.
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
    Container,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, TypedDict, override

from pydantic_ai import AgentRunResultEvent, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import DeferredToolResults
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    DynamicToolUIPart,
    ToolApprovalResponded,
    ToolUIPart,
    UIMessage,
)
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, DataChunk, ErrorChunk
from starlette.responses import Response

from ..agents.subagent_events import SubagentUpdate
from ..db._common import new_id
from ..db.conversations import is_user_request
from ..llm import is_context_overflow

__all__ = [
    "APPROVED_CALLS_KEY",
    "CONTEXT_LENGTH_EXCEEDED",
    "SDK_VERSION",
    "SUBAGENT_CHUNK_TYPE",
    "BranchInfo",
    "ChatAdapter",
    "chat_error_text",
    "close_orphan_tool_calls",
    "decline_pending_approvals",
    "dump_messages_with_ids",
    "record_approvals",
    "record_turn_error",
    "run_and_persist",
]

logger = logging.getLogger(__name__)

CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"

# Vercel AI data-stream protocol version the frontend speaks; request parsing
# and response dumping must agree on it. The frontend runs AI SDK v7 (`ai@7`,
# `@ai-sdk/react@4`), whose wire equals v6's, so this only reflects the client's
# real SDK major and reserves the value for future v7-only chunks.
SDK_VERSION = 7

# Transient data-part type carrying live subagent transcript snapshots; the
# frontend consumes these via `useChat({ onData })` (see `lib/chat/subagent.ts`).
SUBAGENT_CHUNK_TYPE = "data-subagent"

# Message metadata key carrying per-reasoning-block stream durations in
# milliseconds, ordered by the reasoning parts in the assistant response.
REASONING_DURATIONS_KEY = "reasoningDurationsMs"

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

# Message metadata key carrying a run's error text so the frontend can re-render
# the chat error banner from reloaded history (a stream error is otherwise a
# transient chunk, not a message part, and is lost on reload).
CHAT_ERROR_KEY = "chatError"

# Message metadata key listing the tool calls a user approval released, set on
# the request that carries their returns (see `record_approvals`).
APPROVED_CALLS_KEY = "approvedToolCalls"

type PersistTurn = Callable[[Sequence[ModelMessage]], Awaitable[None]]


class BranchInfo(TypedDict):
    """Branch annotation merged into a forking node's ``UIMessage`` metadata.

    The camelCase keys are the wire shape the frontend reads; index and count
    are derived here from the sibling list the repository returns.
    """

    branchCount: int
    branchIndex: int
    siblingIds: list[str]


def chat_error_text(error: Exception) -> str:
    """Serialize *error* for the chat stream.

    Context-window overflows are prefixed with the stable
    ``context_length_exceeded`` code so the frontend can offer compaction
    with an exact match instead of fuzzy-matching provider prose.
    """
    if is_context_overflow(error):
        return f"{CONTEXT_LENGTH_EXCEEDED}: {error}"
    return str(error)


@dataclass
class ChatEventStream[DepsT, OutputT](VercelAIEventStream[DepsT, OutputT]):
    """Event stream that rewrites error chunks with canonical codes and records
    how long each thinking part took to stream.

    Vercel AI and Pydantic AI do not expose a first-class reasoning duration.
    We persist this as application message metadata instead of provider
    metadata, so it stays UI-owned and is not sent back to providers as part
    metadata on follow-up turns.
    """

    _thinking_started_at: float | None = None
    _thinking_durations_ms: list[int] = field(default_factory=list[int])

    async def on_error(self, error: Exception) -> AsyncIterator[BaseChunk]:
        async for chunk in super().on_error(error):
            if isinstance(chunk, ErrorChunk):
                chunk = ErrorChunk(error_text=chat_error_text(error))
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
        if self._thinking_durations_ms:
            response = event.result.response
            response.metadata = {
                **(response.metadata or {}),
                REASONING_DURATIONS_KEY: self._thinking_durations_ms,
            }

        async for chunk in super().handle_run_result(event):
            yield chunk


class ChatAdapter[DepsT, OutputT](VercelAIAdapter[DepsT, OutputT]):
    """``VercelAIAdapter`` wired to :class:`ChatEventStream`, client echo dropped."""

    def build_event_stream(self) -> ChatEventStream[DepsT, OutputT]:
        return ChatEventStream(
            self.run_input,
            accept=self.accept,
            sdk_version=self.sdk_version,
            server_message_id=self.server_message_id,
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
        unaffected: :attr:`deferred_tool_results` reads them from the request
        itself rather than from these messages.
        """
        return self.load_messages(
            [message for message in self.run_input.messages if message.role == "user"]
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


def record_approvals(
    messages: Sequence[ModelMessage], results: DeferredToolResults | None
) -> None:
    """Store which of this turn's calls a user approval released, for reload.

    A denial is self-describing: its return carries ``outcome='denied'`` and the
    reason, so it projects back to the refusal the user saw.  An approval is
    not.  The call simply runs and stores an ordinary successful return, so
    nothing in the message list says it was ever gated, and the "Approved" line
    that was on screen live is gone on reload while a denial's survives.

    The decision is recorded on the request carrying the released call's return,
    which is UI-owned metadata that never reaches the provider, like the
    reasoning durations and the turn error beside it.  The return part's own
    ``metadata`` would be the closer home but is already the tool-output chunk
    channel (see ``tools.pydantic_ai.wrap_tool_output``).
    :func:`dump_messages_with_ids` reads it back.  Mutates in place.
    """
    approved = {
        call_id
        for call_id, decision in (results.approvals if results else {}).items()
        if decision is True
    }
    if not approved:
        return

    for message in messages:
        if not isinstance(message, ModelRequest):
            continue

        released = [
            part.tool_call_id
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_call_id in approved
        ]
        if released:
            message.metadata = {
                **(message.metadata or {}),
                APPROVED_CALLS_KEY: released,
            }


def _attach_approvals(messages: Iterable[UIMessage], approved: Container[str]) -> None:
    """Re-attach the approval decision to the parts of *approved* calls.

    The counterpart of :func:`record_approvals`: what the run does not record,
    the projection puts back, so an approved call reloads with the same chip a
    denied one does.  The approval id is not a match key (``tool_call_id`` is),
    so it is derived rather than minted, as upstream does for a pending one.
    """
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ToolUIPart | DynamicToolUIPart)
                and part.approval is None
                and part.tool_call_id in approved
            ):
                part.approval = ToolApprovalResponded(
                    id=part.tool_call_id, approved=True
                )


def dump_messages_with_ids(
    pairs: Sequence[tuple[str, ModelMessage]],
    *,
    siblings: Mapping[str, Sequence[str]] | None = None,
) -> list[UIMessage]:
    """Project ``(node_id, message)`` pairs to ``UIMessage``s keyed by node id.

    Each ``UIMessage.id`` is set to its head node's id, so the client's edit /
    regenerate / branch-select ``messageId`` maps straight back to a tree node.
    The adapter invokes the id generator with the exact objects passed here (it
    iterates this same list), so the identity lookup is an exact match and
    stays correct even when one request emits two ``UIMessage``s (system +
    user); a tool-return-only request emits none and is never looked up.  An
    unmapped object — unreachable today — degrades to a fresh non-addressable id
    rather than raising.  When given, *siblings* maps a forking node to its
    ordered sibling ids; its :class:`BranchInfo` is merged under a ``branch``
    metadata key (leaving the framework key intact) so the frontend can render
    branch navigation.  Calls released by an approval are re-annotated with the
    decision the run itself does not record (see :func:`record_approvals`).
    """
    node_by_obj = {id(msg): node_id for node_id, msg in pairs}

    if siblings:
        for node_id, msg in pairs:
            sibs = siblings.get(node_id)
            if sibs is not None:
                sib_ids = list(sibs)
                branch = BranchInfo(
                    branchCount=len(sib_ids),
                    branchIndex=sib_ids.index(node_id),
                    siblingIds=sib_ids,
                )
                msg.metadata = {**(msg.metadata or {}), "branch": branch}

    def assign_id(msg: ModelMessage, _role: str, _index: int) -> str:
        return node_by_obj.get(id(msg)) or new_id()

    ui_messages = ChatAdapter.dump_messages(
        [msg for _, msg in pairs],
        generate_message_id=assign_id,
        sdk_version=SDK_VERSION,
    )
    _attach_approvals(
        ui_messages,
        frozenset(
            call_id
            for _, msg in pairs
            if isinstance(released := (msg.metadata or {}).get(APPROVED_CALLS_KEY), list)
            for call_id in released
        ),
    )
    return ui_messages


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
    resolved = {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, (ToolReturnPart, RetryPromptPart))
    }
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


def decline_pending_approvals(prefix: Sequence[ModelMessage]) -> ModelRequest | None:
    """Refuse the approval requests *prefix* ends on, or ``None`` if it ends on none.

    A run that finishes awaiting approval leaves its call dangling on purpose,
    so the next request can carry the decision and resume it.  A next request
    that carries a new prompt instead ends that run for good: the call can never
    be answered now, yet it stays in the stored history, where pydantic-ai
    repairs it on the way to the provider with a generic ``interrupted``
    result.  The model reads that as a transient failure and reissues the
    identical call, which is left dangling in turn — so a single abandoned
    approval has every later turn repeat the same call with the same arguments.

    Answering it as a denial instead says what actually happened, and once the
    refusal is stored the history holds a resolved call rather than one that is
    repaired again on every future turn.

    Only a trailing response is considered, and then every call it holds is
    unanswered by construction: a result is a ``ModelRequest`` part, so it can
    only sit *after* the response that called for it, and nothing sits after
    the last message.  An earlier dangling call needs no handling here — it was
    already closed when its own turn ended.
    """
    if not prefix or not isinstance(last := prefix[-1], ModelResponse):
        return None

    returns = [
        ToolReturnPart(
            tool_name=part.tool_name,
            content=ABANDONED_APPROVAL_DENIAL,
            tool_call_id=part.tool_call_id,
            outcome="denied",
        )
        for part in last.parts
        if isinstance(part, ToolCallPart)
    ]

    return ModelRequest(parts=returns) if returns else None


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


async def run_and_persist[DepsT, OutputT](
    adapter: VercelAIAdapter[DepsT, OutputT],
    stream: AsyncIterator[BaseChunk],
    *,
    persist: PersistTurn,
    subagent_sink: asyncio.Queue[SubagentUpdate] | None = None,
) -> Response:
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
    a trailing error chunk; on a client disconnect the write is shielded and a
    failure can only be logged.

    Every write goes through :func:`record_approvals` first, so a call the user
    released is stored with that fact whichever way the turn ended.
    """

    async def persist_turn(messages: Sequence[ModelMessage]) -> None:
        record_approvals(messages, adapter.deferred_tool_results)
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
                # Client disconnect / cancellation: close any in-flight call so
                # it does not reload as an approval, persist what completed
                # (shielded — the client is gone, so a failure can only be
                # logged) and propagate.
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
                yield ErrorChunk(error_text="Failed to save the conversation.")

    return adapter.streaming_response(relay())
