"""Vercel AI chat adapter for the server-owned conversation tree.

The adapter replays history from storage rather than from the browser echo, and
its event stream records UI-owned metadata and shows compactions live.

It also owns :func:`run_and_persist`, which streams a turn and hands the
run's message list to storage on every finish.  What a turn stores and resumes
lives in :mod:`~hivegent.server.turns`.
"""

import logging
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any, Literal, override

from exar.helpers import shield_to_completion
from exar.pydantic_ai.compaction import Compacted
from pydantic_ai import (
    AgentRunEvents,
    AgentRunResult,
    AgentRunResultEvent,
    BinaryContent,
    capture_run_messages,
)
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    CapabilityEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    DynamicToolApprovalRespondedPart,
    FileUIPart,
    ToolApprovalResponded,
    ToolApprovalRespondedPart,
    ToolOutputAvailablePart,
    ToolOutputErrorPart,
    UIMessage,
)
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk

from ...agents.approval import APPROVAL_NOTE, APPROVED_CHANGES, changes_digest
from ...agents.common import ASK_USER_TOOL
from ...agents.tools.ask import ANSWERS, DISMISSED_QUESTIONS
from ...agents.transcripts import SubagentSteps
from ...db.conversations import prompt_items
from ...shared.l10n import Localized
from ..turns import (
    INTERRUPTED_TOOL_ERROR,
    RUN_ERROR_CLOSED,
    close_orphan_tool_calls,
    record_approvals,
    record_turn_error,
)

__all__ = [
    "APPROVAL_ARGS_KEY",
    "APPROVAL_METADATA_KEY",
    "REASONING_DURATIONS_KEY",
    "SDK_VERSION",
    "ChatAdapter",
    "ChatEventStream",
    "PersistTurn",
    "RunEnd",
    "StaleAnswers",
    "agent_events",
    "denial_message",
    "logger",
    "run_and_persist",
]

logger = logging.getLogger(__name__)

# Vercel AI data-stream protocol version the frontend speaks; request parsing
# and response dumping must agree on it. The frontend runs AI SDK v7 (`ai@7`,
# `@ai-sdk/react@4`), whose wire equals v6's, so this only reflects the client's
# real SDK major and reserves the value for future v7-only chunks.
SDK_VERSION = 7


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

# Message metadata key mapping each approval-pending tool call id to the
# arguments its tool runs with once approved, validated and repaired, which
# may differ from those the model sent.
APPROVAL_ARGS_KEY = "approvalArgs"


_ASK_USER_PART = f"tool-{ASK_USER_TOOL}"

type PersistTurn = Callable[[Sequence[ModelMessage]], Awaitable[None]]


class StaleAnswers(Exception):
    """A request answers calls the stored conversation no longer awaits, so its client is behind."""


@dataclass(slots=True)
class RunEnd:
    """What a run ended on, set once it finished cleanly."""

    result: AgentRunResult[Any] | None = None

    @property
    def deferred(self) -> DeferredToolRequests | None:
        """The calls the run paused for, ``None`` unless it paused.

        Without what they were deferred with and the arguments they run with,
        which the response that asked stores once for the client
        (:data:`APPROVAL_METADATA_KEY`, :data:`APPROVAL_ARGS_KEY`), and which a
        resume validates again.
        """
        output = None if self.result is None else self.result.output

        return (
            replace(output, metadata={}, validated_args={})
            if isinstance(output, DeferredToolRequests)
            else None
        )


async def agent_events[OutputT](
    run: AbstractAsyncContextManager[AgentRunEvents[OutputT]], end: RunEnd | None = None
) -> AsyncIterator[NativeEvent]:
    """The events of *run*, an agent's ``run_stream_events``, for ``transform_stream``.

    The run's result lands on *end* when it finishes.
    """
    async with run as events:
        async for event in events:
            if end is not None and isinstance(event, AgentRunResultEvent):
                end.result = event.result

            yield event


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
    """Event stream that records how long each thinking part took to stream,
    and shows compactions and the steps of subagents as they happen.

    Vercel AI and Pydantic AI do not expose a first-class reasoning duration.
    We persist this as application message metadata instead of provider
    metadata, so it stays UI-owned and is not sent back to providers as part
    metadata on follow-up turns.

    The ``DeferredToolRequests`` a run ended on are kept on the conversation's
    record, never sent to the client.  What each ``ApprovalRequired`` carried is
    stored on the response under :data:`APPROVAL_METADATA_KEY` alone, for the
    client, live in the run's one metadata chunk and on reload alike, since the
    protocol's approval request chunk carries ids only, and for the resume,
    which binds an approval to it.  The arguments each call runs with go under
    :data:`APPROVAL_ARGS_KEY` the same way, so the user approves those.
    """

    _thinking_started_at: float | None = None
    _thinking_durations_ms: list[int] = field(default_factory=list[int])
    subagents: SubagentSteps = field(default_factory=SubagentSteps)

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

    @override
    async def handle_capability_event(
        self, event: CapabilityEvent
    ) -> AsyncIterator[BaseChunk]:
        """Show a compaction the run made, as the stored one reloads, and what its subagents do under their calls."""
        if isinstance(event, Compacted):
            async for chunk in self.handle_compaction(event.part):
                yield chunk

        elif (update := self.subagents.update(event)) is not None:
            yield update.chunk()

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
            for key, by_call in (
                (APPROVAL_METADATA_KEY, output.metadata),
                (APPROVAL_ARGS_KEY, output.validated_args),
            ):
                if approvals := {
                    call.tool_call_id: by_call[call.tool_call_id]
                    for call in output.approvals
                    if call.tool_call_id in by_call
                }:
                    ui_metadata[key] = approvals

        if ui_metadata:
            response.metadata = {**(response.metadata or {}), **ui_metadata}

        async for chunk in super().handle_run_result(event):
            yield chunk


@dataclass
class ChatAdapter[DepsT, OutputT](VercelAIAdapter[DepsT, OutputT]):
    """``VercelAIAdapter`` wired to :class:`ChatEventStream`, client echo dropped.

    An attachment's filename, which the base adapter drops, is kept as the
    identifier of its ``BinaryContent``, which no model reads in a user prompt.

    Args:
        subagents: The steps of the turn's subagents, which its stream shows
            live and the turn stores, so a reload shows what streamed.
    """

    subagents: SubagentSteps = field(default_factory=SubagentSteps)

    @classmethod
    @override
    def load_messages(cls, messages: Sequence[UIMessage]) -> list[ModelMessage]:
        filenames = {
            part.url: part.filename
            for message in messages
            if message.role == "user"
            for part in message.parts
            if isinstance(part, FileUIPart) and part.filename
        }
        loaded = super().load_messages(messages)

        for message in loaded:
            for part in message.parts if isinstance(message, ModelRequest) else ():
                if isinstance(part, UserPromptPart) and not isinstance(
                    part.content, str
                ):
                    part.content = [_named(item, filenames) for item in part.content]

        return loaded

    @classmethod
    @override
    def dump_messages(
        cls,
        messages: Sequence[ModelMessage],
        *,
        generate_message_id: Callable[
            [ModelRequest | ModelResponse, Literal["system", "user", "assistant"], int],
            str,
        ]
        | None = None,
        sdk_version: Literal[5, 6, 7] = 5,
    ) -> list[UIMessage]:
        filenames = {
            item.data_uri: item.identifier
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in prompt_items(part)
            if isinstance(item, BinaryContent) and _filename(item)
        }
        dumped = super().dump_messages(
            messages, generate_message_id=generate_message_id, sdk_version=sdk_version
        )

        for message in dumped:
            for part in message.parts if message.role == "user" else ():
                if isinstance(part, FileUIPart):
                    part.filename = filenames.get(part.url, part.filename)

        return dumped

    def build_event_stream(self) -> ChatEventStream[DepsT, OutputT]:
        return ChatEventStream(
            self.run_input,
            accept=self.accept,
            sdk_version=self.sdk_version,
            server_message_id=self.server_message_id,
            manage_system_prompt=self.manage_system_prompt,
            subagents=self.subagents,
        )

    @cached_property
    @override
    def messages(self) -> list[ModelMessage]:
        """The client's own new prompt only, the server owns the rest.

        The base adapter appends the request's messages to the caller's
        ``message_history``, and the SDK's approval continuation re-sends the
        assistant message holding the pending call, already the last message
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
        self, pending: DeferredToolRequests | None, history: Sequence[ModelMessage]
    ) -> DeferredToolResults | None:
        """Resume the run on *history* with the user's decisions, notes, and answers to *pending*.

        *pending* are the calls the conversation's record awaits.  An answer to
        a call that is no longer pending is dropped, as the client re-sends
        every settled card of the message, while a decision only ever answers
        a call it shows as awaiting one.  So a decision for any other call, or a
        request without a prompt that answers nothing pending, which only
        continues a run by answering it, means the client is behind the store,
        and the request is refused, as pydantic-ai's adapter refuses it.  The
        client's ``reason`` is the user's own note on either decision.  A
        denial wraps it into the refusal the model reads
        (:func:`denial_message`) and an approval hands it to the call as
        metadata, which :class:`~hivegent.agents.approval.ApprovalNotes`
        appends to its result.  An approval is bound to the changes the call
        asked with, as the response that asked in *history* stores them
        (:data:`APPROVAL_METADATA_KEY`), so the write gate refuses it once they
        planned to others.  Answers to questions are the results of their
        calls as they stand.

        Raises:
            StaleAnswers: If the client is behind the store.
            ValueError: If an answer does not fit its schema (a
                ``pydantic.ValidationError``), or a call is answered with the
                other kind of result, such as an approval answered like a question.
        """
        decisions = self.approval_decisions
        awaiting = _awaiting_approval(pending)

        if not awaiting.issuperset(decisions):
            raise StaleAnswers

        open_ids = (
            awaiting
            if pending is None
            else awaiting | {call.tool_call_id for call in pending.calls}
        )
        answers = {
            call_id: answer
            for call_id, answer in (self.question_answers if open_ids else {}).items()
            if call_id in open_ids
        }

        if pending is None or not (decisions or answers):
            if self.prompt is None and not self._regenerates():
                raise StaleAnswers

            return None

        asked = {
            call_id: summary
            for message in history
            if isinstance(message, ModelResponse) and message.metadata
            for call_id, summary in message.metadata.get(
                APPROVAL_METADATA_KEY, {}
            ).items()
        }

        return pending.build_results(
            calls=answers,
            approvals={
                call_id: True
                if decision.approved
                else ToolDenied(denial_message(decision.reason))
                for call_id, decision in decisions.items()
            },
            metadata={
                call_id: {
                    APPROVED_CHANGES: changes_digest(asked.get(call_id)),
                    **({APPROVAL_NOTE: decision.reason} if decision.reason else {}),
                }
                for call_id, decision in decisions.items()
                if decision.approved
            },
        )


@asynccontextmanager
async def _closing[T](iterator: AsyncIterator[T]) -> AsyncGenerator[AsyncIterator[T]]:
    """*iterator*, closed on exit if it is a generator, as ``contextlib.aclosing`` closes one typed as such."""
    try:
        yield iterator
    finally:
        if isinstance(iterator, AsyncGenerator):
            await iterator.aclose()


def _filename(item: BinaryContent) -> bool:
    """Whether *item* is identified by a filename rather than by its content hash."""
    return (
        item.identifier
        != BinaryContent(item.data, media_type=item.media_type).identifier
    )


def _named(item: UserContent, filenames: Mapping[str, str]) -> UserContent:
    """*item*, identified by its filename among *filenames* when it is an attachment named there."""
    if (
        not isinstance(item, BinaryContent)
        or (name := filenames.get(item.data_uri)) is None
    ):
        return item

    return BinaryContent.narrow_type(
        BinaryContent(
            item.data,
            media_type=item.media_type,
            identifier=name,
            vendor_metadata=item.vendor_metadata,
        )
    )


def _awaiting_approval(pending: DeferredToolRequests | None) -> set[str]:
    """The ids of the *pending* calls that await an approval."""
    return (
        set() if pending is None else {call.tool_call_id for call in pending.approvals}
    )


async def _persist_safely(
    persist: PersistTurn, messages: Sequence[ModelMessage]
) -> bool:
    """Run *persist*, logging instead of raising on failure, and return whether it succeeded.

    It fires after the response has already streamed, possibly during
    client-disconnect teardown, so a storage error must not crash the
    request; it is logged for follow-up instead.
    """
    try:
        await persist(messages)
    except Exception:
        logger.exception("Failed to persist conversation turn")

        return False

    return True


def run_and_persist[DepsT, OutputT](
    adapter: ChatAdapter[DepsT, OutputT],
    stream: AsyncIterator[BaseChunk],
    *,
    persist: PersistTurn,
) -> AsyncIterator[BaseChunk]:
    """Stream a chat turn and persist the run's message list on every finish.

    *stream* is the still-unstarted chunk stream from ``adapter.transform_stream``;
    iterating it inside ``capture_run_messages`` exposes the run's live message
    list, so whatever exists when the stream ends is persisted: the full turn on
    a clean finish, or the prompt plus completed messages on an error (the
    Vercel adapter turns run errors into an in-band error chunk, so the stream
    still ends normally).  An answer cut off mid-stream is captured by
    pydantic-ai upstream into ``capture_run_messages``, so this path persists
    whatever it holds.

    On a run error (streamed in-band as an ``ErrorChunk``), the turn is
    normalized before persisting so reload mirrors what streamed:
    :func:`close_orphan_tool_calls` closes the call the error aborted, as the
    stream closed it, and
    :func:`record_turn_error` stores the error text for the reloaded banner.  A
    clean finish is persisted untouched, so a genuine approval-pending call stays
    dangling and reloads as an approval request rather than an error.

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

    async def relay() -> AsyncIterator[BaseChunk]:
        error_text: str | None = None
        with capture_run_messages() as captured:
            try:
                # Closed in this task however the turn ends, so the generators
                # of its run, which hold its session and context variables
                # across their yields, never finalize from the garbage collector.
                async with _closing(stream):
                    async for chunk in stream:
                        # A run error is streamed in-band, not raised, so its
                        # text is kept to normalize the errored turn below.
                        if isinstance(chunk, ErrorChunk):
                            error_text = chunk.error_text

                        yield chunk
            except BaseException:
                # A cancel: close any in-flight call so it does not reload as
                # an approval, persist what completed (to completion, so the
                # turn ends only once it is stored, and a failure can only be
                # logged) and propagate.
                _ = await shield_to_completion(
                    _persist_safely(
                        persist_turn,
                        close_orphan_tool_calls(
                            list(captured), INTERRUPTED_TOOL_ERROR, "interrupted"
                        ),
                    )
                )
                raise

            messages = list(captured)

            if error_text is not None:
                messages = close_orphan_tool_calls(messages, RUN_ERROR_CLOSED, "failed")
                record_turn_error(messages, error_text)

            if not await shield_to_completion(_persist_safely(persist_turn, messages)):
                yield ErrorChunk(error_text=_SAVE_FAILED.current)

    return relay()
