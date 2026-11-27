"""Chat persistence without a database.

``run_and_persist`` hands the persist callback the turn however it ended,
``Replay`` and ``kept_before`` say what a resume, an edit, or a regenerate runs
on, and ``dump_transcript`` projects the stored messages to one ``UIMessage``
per UI message id.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from time import monotonic
from typing import TypedDict, Unpack

import pytest
from exar.agents import RunConfig
from exar.policy import allow_all
from exar.pydantic_ai import traced_events
from exar.pydantic_ai.compaction import Compacted, is_compaction
from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai import Agent, BinaryContent, DeferredToolRequests, RunContext
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelSettingsDeltaPart,
    PartStartEvent,
    RetryFeedbackPart,
    RetryInfo,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.output import OutputSpec
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.vercel_ai.request_types import (
    FileUIPart,
    RegenerateMessage,
    SubmitMessage,
    TextUIPart,
    ToolApprovalRequestedPart,
    ToolApprovalResponded,
    ToolApprovalRespondedPart,
    ToolInputAvailablePart,
    ToolOutputAvailablePart,
    ToolOutputErrorPart,
    UIMessage,
)
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, DataChunk
from starlette.responses import StreamingResponse

from hivegent.agents.approval import (
    APPROVED_CHANGES,
    ApprovalNotes,
    approval_note_text,
    changes_digest,
)
from hivegent.agents.tools.ask import DISMISSED_QUESTIONS, ask_user
from hivegent.config import settings
from hivegent.db.conversations import (
    CHAT_ERROR_KEY,
    import_conversation,
    is_user_request,
)
from hivegent.db.conversations.postgres import _still_pending
from hivegent.server.export import instruction_snapshots
from hivegent.server.routes.conversations.attachments import accept_imported
from hivegent.server.turns import (
    INTERRUPTED_TOOL_ERROR,
    Replay,
    abandon_pending_calls,
    close_orphan_tool_calls,
    kept_before,
    stamp,
    ui_id,
)
from hivegent.server.vercel import (
    APPROVAL_METADATA_KEY,
    REASONING_DURATIONS_KEY,
    SDK_VERSION,
    ChatAdapter,
    PersistTurn,
    StaleAnswers,
    agent_events,
    denial_message,
    dump_transcript,
    load_archived,
    run_and_persist,
    stream_response,
)
from hivegent.types import (
    ClientConversation,
    ConversationArchive,
    ServerConversation,
)
from tests.helpers import owned, sse_chunks


def _texts(messages: Sequence[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, (UserPromptPart, TextPart))
        and isinstance(part.content, str)
    ]


def _parts[T](messages: Sequence[ModelMessage], kind: type[T]) -> list[T]:
    """Every part of *kind* across *messages*, in order."""
    return [
        part for message in messages for part in message.parts if isinstance(part, kind)
    ]


def pending_requests(messages: Sequence[ModelMessage]) -> DeferredToolRequests | None:
    """The calls the last response of *messages* awaits, as a paused run's record keeps them.

    A question awaits an answer, any other call an approval.
    """
    answered = {part.tool_call_id for part in _parts(messages, ToolReturnPart)}
    response = next(
        (m for m in reversed(messages) if isinstance(m, ModelResponse)), None
    )
    dangling = [
        call
        for call in (response.tool_calls if response else [])
        if call.tool_call_id not in answered
    ]

    if not dangling:
        return None

    return DeferredToolRequests(
        calls=[c for c in dangling if c.tool_name == "ask_user"],
        approvals=[c for c in dangling if c.tool_name != "ask_user"],
    )


def _ui_messages(messages: Sequence[ModelMessage]) -> list[UIMessage]:
    """Project *messages* the way a reload does."""
    return dump_transcript(messages, pending_requests(messages))


def _tool_cards(messages: Sequence[ModelMessage]) -> list[object]:
    """The tool cards a message list reloads as."""
    return [
        part
        for message in _ui_messages(messages)
        for part in message.parts
        if str(getattr(part, "type", "")).startswith("tool-")
    ]


def _tool_states(messages: Sequence[ModelMessage]) -> list[str | None]:
    """Reload states of the tool cards."""
    return [getattr(card, "state", None) for card in _tool_cards(messages)]


def _tool_approvals(
    messages: Sequence[ModelMessage],
) -> list[tuple[bool | None, str | None]]:
    """Approval decisions and notes the tool cards carry."""
    approvals = [getattr(card, "approval", None) for card in _tool_cards(messages)]

    return [
        (getattr(approval, "approved", None), getattr(approval, "reason", None))
        for approval in approvals
    ]


def _prompt(text: str = "q1") -> list[UIMessage]:
    return [UIMessage(id="m1", role="user", parts=[TextUIPart(text=text)])]


class _Options(TypedDict, total=False):
    model: Model
    tool_raises: bool
    tool_needs_approval: bool
    tool_runs: bool
    asks_user: bool
    regenerate: bool
    clock: Callable[[], float]


def _adapter(
    ui_messages: list[UIMessage], **options: Unpack[_Options]
) -> ChatAdapter[None, str | DeferredToolRequests]:
    output_type: OutputSpec[str | DeferredToolRequests] = (
        [str, DeferredToolRequests]
        if options.get("tool_needs_approval") or options.get("asks_user")
        else str
    )
    agent = Agent[None, str | DeferredToolRequests](
        model=options.get("model") or TestModel(custom_output_text="ANSWER"),
        output_type=output_type,
        deps_type=type(None),
        capabilities=[ApprovalNotes()],
    )

    if options.get("asks_user"):
        agent.tool_plain(ask_user)

    if options.get("tool_raises"):

        @agent.tool_plain
        def boom() -> str:
            raise RuntimeError("simulated tool failure mid-run")

    if options.get("tool_runs"):

        @agent.tool_plain
        def lookup() -> str:
            return "found"

    if options.get("tool_needs_approval"):

        @agent.tool
        def write(ctx: RunContext[None], value: str) -> str:
            if not ctx.tool_call_approved:
                raise ApprovalRequired({"value": value})

            return "written"

    return ChatAdapter[None, str | DeferredToolRequests](
        agent=agent,
        run_input=RegenerateMessage(
            id="c1", messages=ui_messages, trigger="regenerate-message", message_id=None
        )
        if options.get("regenerate")
        else SubmitMessage(id="c1", messages=ui_messages, trigger="submit-message"),
        sdk_version=SDK_VERSION,
        clock=options.get("clock", monotonic),
    )


def _stream(
    adapter: ChatAdapter[None, str | DeferredToolRequests],
    persist: PersistTurn,
    history: Sequence[ModelMessage] = (),
) -> StreamingResponse:
    """The turn of *adapter* on *history*, as the route streams it."""
    events = adapter.agent.run_stream_events(
        adapter.prompt,
        message_history=list(history),
        deferred_tool_results=adapter.resume(pending_requests(history), history),
    )
    chunks = adapter.transform_stream(
        traced_events(
            agent_events(events), "q", RunConfig(policy=allow_all), name="chat"
        )
    )

    return stream_response(run_and_persist(adapter, chunks, persist=persist))


async def _run_turn(
    ui_messages: list[UIMessage],
    *,
    message_history: Sequence[ModelMessage] = (),
    persist: PersistTurn | None = None,
    **options: Unpack[_Options],
) -> tuple[list[list[ModelMessage]], str]:
    """Run one turn through ``run_and_persist``, returning the messages it persisted and its body."""
    recorded: list[list[ModelMessage]] = []

    async def record(messages: Sequence[ModelMessage]) -> None:
        recorded.append(list(messages))

    response = _stream(
        _adapter(ui_messages, **options), persist or record, message_history
    )
    body = "".join(
        [
            chunk.decode() if isinstance(chunk, bytes) else str(chunk)
            async for chunk in response.body_iterator
        ]
    )

    return recorded, body


def _replying(seen: list[ModelMessage]) -> FunctionModel:
    """Answer, recording the messages it was sent."""

    async def respond(
        messages: Sequence[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        seen.extend(messages)
        yield "ANSWER"

    return FunctionModel(stream_function=respond)


async def test_closing_a_turn_closes_its_events_in_the_same_task() -> None:
    """A turn closed early leaves no generator of its run to the garbage collector."""
    log: list[str] = []

    async def events() -> AsyncGenerator[NativeEvent]:
        try:
            log.append("started")
            yield PartStartEvent(index=0, part=TextPart("a"))
            yield PartStartEvent(index=1, part=TextPart("b"))
        finally:
            log.append("closed")

    async def persist(_messages: Sequence[ModelMessage]) -> None:
        log.append("persisted")

    adapter = _adapter(_prompt())
    chunks = run_and_persist(
        adapter, adapter.transform_stream(events()), persist=persist
    )
    assert isinstance(chunks, AsyncGenerator)

    async for _ in chunks:
        if log:
            break

    await chunks.aclose()

    assert log == ["started", "closed", "persisted"]


_ANSWERED = [
    ModelRequest(parts=[UserPromptPart(content="q1")]),
    ModelResponse(parts=[TextPart(content="a1")], model_name="m"),
]
_COMPACTION = ModelResponse(parts=[CompactionPart(content="the gist")])
_CLOSED = ModelRequest(
    parts=[ToolReturnPart(tool_name="write", content="denied", tool_call_id="c")]
)


@pytest.mark.parametrize(
    "prefix",
    [
        _ANSWERED,
        [*_ANSWERED, _COMPACTION],
        [*_ANSWERED, _COMPACTION, *_ANSWERED],
        [
            _ANSWERED[0],
            ModelResponse(parts=[ToolCallPart(tool_name="write", tool_call_id="c")]),
            _CLOSED,
        ],
        _ANSWERED[:1],
    ],
    ids=[
        "after-answer",
        "after-compaction",
        "past-compaction",
        "after-closed-call",
        "after-failed-prompt",
    ],
)
async def test_a_turn_stores_only_its_own_messages_past_the_replay(
    prefix: list[ModelMessage],
) -> None:
    """The run merges consecutive requests, so the prompt can sit in a replayed one."""
    recorded, _ = await _run_turn(_prompt("q2"), message_history=prefix)
    turn = Replay.of(prefix).turn(recorded[0])

    assert turn.above is None
    assert _texts(turn.messages) == ["q2", "ANSWER"]
    assert not _parts(turn.messages, CompactionPart)
    assert not _parts(turn.messages, ToolReturnPart)


async def test_regenerate_reruns_from_history_without_a_new_message() -> None:
    """The frontend sends no message for a regenerate, which answers the replayed prompt."""
    prefix: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="q1")])]
    recorded, _ = await _run_turn([], message_history=prefix, regenerate=True)

    assert _texts(recorded[0][len(prefix) :]) == ["ANSWER"]


async def test_errored_turn_closes_its_dangling_tool_call() -> None:
    """A raised tool leaves a call without a return, which reloads as failed, not as an approval prompt."""
    recorded, _ = await _run_turn(_prompt(), tool_raises=True)
    messages = recorded[0]
    call = _parts(messages, ToolCallPart)[0]
    (ret,) = _parts(messages, ToolReturnPart)

    assert ret.tool_call_id == call.tool_call_id
    assert ret.outcome == "failed"
    assert "simulated tool failure" not in str(ret.content)
    assert _tool_states(messages) == ["output-error"]


async def _pending_approval() -> list[ModelMessage]:
    recorded, _ = await _run_turn(_prompt(), tool_needs_approval=True)

    return recorded[0]


def _answer_approval(
    pending: Sequence[ModelMessage], *, approved: bool, note: str | None = None
) -> UIMessage:
    """Echo the assistant message holding the pending call, approval answered, as the AI SDK re-sends it."""
    message = _ui_messages(pending)[-1]
    message.parts = [
        ToolApprovalRespondedPart(
            type=part.type,
            tool_call_id=part.tool_call_id,
            input=part.input,
            approval=ToolApprovalResponded(
                id=part.tool_call_id, approved=approved, reason=note
            ),
        )
        for part in message.parts
        if isinstance(part, ToolApprovalRequestedPart)
    ]

    return message


async def test_a_turn_awaiting_approval_keeps_its_call_open_and_shows_what_was_asked() -> (
    None
):
    """No error streamed, so the call reloads as an approval request carrying its metadata, as streamed."""
    recorded, body = await _run_turn(_prompt(), tool_needs_approval=True)
    call = _parts(recorded[0], ToolCallPart)[0]
    expected = {call.tool_call_id: {"value": call.args_as_dict()["value"]}}
    streamed = [
        chunk["messageMetadata"]
        for chunk in sse_chunks(body)
        if chunk["type"] == "message-metadata"
    ]
    reloaded = _ui_messages(recorded[0])[-1].metadata

    assert not _parts(recorded[0], ToolReturnPart)
    assert _tool_states(recorded[0]) == ["approval-requested"]
    assert streamed[-1][APPROVAL_METADATA_KEY] == expected
    assert isinstance(reloaded, dict)
    assert reloaded[APPROVAL_METADATA_KEY] == expected


@pytest.mark.parametrize("note", [None, "Keep a copy of the old version."])
@pytest.mark.parametrize("approved", [True, False])
async def test_an_answered_approval_resolves_the_stored_call_once(
    approved: bool, note: str | None
) -> None:
    """The model reads the decision and its note, the reload shows them as typed, and the stored call is not replayed from the echo."""
    pending = await _pending_approval()
    seen: list[ModelMessage] = []
    recorded, _ = await _run_turn(
        [_answer_approval(pending, approved=approved, note=note)],
        model=_replying(seen),
        message_history=pending,
        tool_needs_approval=True,
    )
    (call,) = _parts(recorded[0], ToolCallPart)
    (ret,) = _parts(recorded[0], ToolReturnPart)

    if not approved:
        expected = denial_message(note)
    else:
        expected = "written" if note is None else ["written", approval_note_text(note)]

    assert ret.tool_call_id == call.tool_call_id
    assert [part.content for part in _parts(seen, ToolReturnPart)] == [expected]
    assert _tool_states(recorded[0]) == [
        "output-available" if approved else "output-denied"
    ]
    assert _tool_approvals(recorded[0]) == [(approved, note)]


async def test_abandoned_approval_is_declined_for_the_next_turn() -> None:
    """A prompt sent instead of an answer denies the pending call, which a generic "interrupted" repair would have the model reissue."""
    pending = await _pending_approval()
    declined = abandon_pending_calls(pending_requests(pending))
    assert declined is not None

    (ret,) = _parts([declined], ToolReturnPart)

    assert ret.outcome == "denied"
    assert ret.tool_call_id == _parts(pending, ToolCallPart)[0].tool_call_id
    assert _tool_states([*pending, declined]) == ["output-denied"]
    assert _tool_approvals([*pending, declined]) == [(False, None)]
    assert abandon_pending_calls(None) is None


@pytest.mark.parametrize("stopped", [False, True])
async def test_a_call_a_stop_closed_is_no_longer_pending(stopped: bool) -> None:
    """A stop after a run paused closes its call, so no later request resolves it again."""
    pending = await _pending_approval()
    requests = pending_requests(pending)

    if stopped:
        pending = close_orphan_tool_calls(
            pending, INTERRUPTED_TOOL_ERROR, "interrupted"
        )

    assert requests is not None
    assert _still_pending(requests, pending) == (None if stopped else requests)


async def test_a_call_run_beside_an_approval_leaves_it_to_abandon() -> None:
    """The turn ends on the result of the call that ran, the gated one still pending."""

    async def respond(
        _messages: Sequence[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[DeltaToolCalls]:
        yield {
            0: DeltaToolCall(name="lookup", json_args="{}"),
            1: DeltaToolCall(name="write", json_args=json.dumps({"value": "v"})),
        }

    recorded, _ = await _run_turn(
        _prompt(),
        model=FunctionModel(stream_function=respond),
        tool_needs_approval=True,
        tool_runs=True,
    )
    declined = abandon_pending_calls(pending_requests(recorded[0]))

    assert isinstance(recorded[0][-1], ModelRequest)
    assert declined is not None
    assert [(r.tool_name, r.outcome) for r in _parts([declined], ToolReturnPart)] == [
        ("write", "denied")
    ]


async def test_a_decision_for_a_call_no_longer_pending_is_refused() -> None:
    """A client behind the store is told so, rather than its decision being dropped."""
    pending = await _pending_approval()
    adapter = _adapter(
        [_answer_approval(pending, approved=True)], tool_needs_approval=True
    )

    with pytest.raises(StaleAnswers):
        adapter.resume(None, pending)


async def test_an_approval_is_bound_to_the_changes_it_was_asked_with() -> None:
    """The gate replans an approved write and refuses it once the changes differ."""
    pending = await _pending_approval()
    requests = pending_requests(pending)
    assert requests is not None
    call_id = requests.approvals[0].tool_call_id
    asked = pending[-1]
    assert isinstance(asked, ModelResponse)
    asked.metadata = {
        APPROVAL_METADATA_KEY: {call_id: {"items": [{"path": "~/a.md"}], "added": 1}}
    }
    adapter = _adapter(
        [_answer_approval(pending, approved=True)], tool_needs_approval=True
    )
    results = adapter.resume(requests, pending)

    assert results is not None
    assert results.metadata[call_id][APPROVED_CHANGES] == changes_digest(
        {"added": 1, "items": [{"path": "~/a.md"}]}
    )


async def test_an_approval_answered_like_a_question_is_rejected() -> None:
    """Each pending call takes only the kind of result it was deferred for."""
    pending = await _pending_approval()
    message = _ui_messages(pending)[-1]
    message.parts = [
        ToolOutputAvailablePart(
            type="tool-ask_user",
            tool_call_id=part.tool_call_id,
            input=part.input,
            output=[{"selected": ["All"]}],
        )
        for part in message.parts
        if isinstance(part, ToolApprovalRequestedPart)
    ]
    adapter = _adapter([message], tool_needs_approval=True, asks_user=True)

    with pytest.raises(ValueError, match="DeferredToolRequests.calls"):
        adapter.resume(pending_requests(pending), pending)


_QUESTIONS = {
    "questions": [
        {
            "header": "Scope",
            "question": "Which scope?",
            "options": [{"label": "All", "recommended": True}, {"label": "Some"}],
        }
    ]
}


def _asking_model(seen: list[ModelMessage]) -> FunctionModel:
    """Ask :data:`_QUESTIONS` on a fresh prompt and answer once resumed."""

    async def respond(
        messages: Sequence[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        seen.extend(messages)

        if isinstance(messages[-1].parts[-1], UserPromptPart):
            yield {0: DeltaToolCall(name="ask_user", json_args=json.dumps(_QUESTIONS))}
        else:
            yield "ANSWER"

    return FunctionModel(stream_function=respond)


async def _pending_question() -> list[ModelMessage]:
    recorded, _ = await _run_turn(_prompt(), model=_asking_model([]), asks_user=True)

    return recorded[0]


def _reply(pending: Sequence[ModelMessage], output: object | None) -> UIMessage:
    """Echo the assistant message with its question answered, or dismissed for ``None``."""
    message = _ui_messages(pending)[-1]
    message.parts = [
        ToolOutputErrorPart(
            type=part.type,
            tool_call_id=part.tool_call_id,
            input=part.input,
            error_text="dismissed",
        )
        if output is None
        else ToolOutputAvailablePart(
            type=part.type,
            tool_call_id=part.tool_call_id,
            input=part.input,
            output=output,
        )
        for part in message.parts
        if isinstance(part, ToolInputAvailablePart)
    ]

    return message


@pytest.mark.parametrize(
    ("output", "state", "content"),
    [
        (
            [{"selected": ["All"], "note": " Skip drafts. "}],
            "output-available",
            [{"selected": ["All"], "note": "Skip drafts."}],
        ),
        (None, "output-error", DISMISSED_QUESTIONS),
    ],
    ids=["answered", "dismissed"],
)
async def test_a_reply_to_a_question_resumes_the_run(
    output: object | None, state: str, content: object
) -> None:
    """The answer, or the dismissal, is the result of the call the model reads."""
    pending = await _pending_question()
    seen: list[ModelMessage] = []
    recorded, _ = await _run_turn(
        [_reply(pending, output)],
        model=_asking_model(seen),
        message_history=pending,
        asks_user=True,
    )

    assert [part.content for part in _parts(seen, ToolReturnPart)] == [content]
    assert "ANSWER" in _texts(recorded[0])
    assert _tool_states(recorded[0]) == [state]


async def test_a_pending_question_reloads_open_and_a_prompt_instead_dismisses_it() -> (
    None
):
    """A deferred question reloads as a question, not an approval, and is dismissed, not denied."""
    pending = await _pending_question()
    closed = abandon_pending_calls(pending_requests(pending))
    assert closed is not None

    assert not _parts(pending, ToolReturnPart)
    assert _tool_states(pending) == ["input-available"]
    assert [part.content for part in _parts([closed], ToolReturnPart)] == [
        DISMISSED_QUESTIONS
    ]
    assert _tool_states([*pending, closed]) == ["output-error"]
    assert _tool_approvals([*pending, closed]) == [(None, None)]


async def test_an_invalid_answer_is_rejected() -> None:
    """The client writes the answer, so one that answers nothing never reaches the model."""
    pending = await _pending_question()
    adapter = _adapter([_reply(pending, [{"selected": []}])], asks_user=True)

    with pytest.raises(ValidationError):
        adapter.resume(pending_requests(pending), pending)


async def test_generic_run_error_is_recorded_for_reload() -> None:
    """A run failing before it responds records the error on its last projecting message, which a reload would otherwise lose."""

    async def fail_immediately(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        raise RuntimeError("provider exploded")
        yield ""  # unreachable, marks this a stream function

    recorded, body = await _run_turn(
        _prompt(), model=FunctionModel(stream_function=fail_immediately)
    )
    target = next(
        message
        for message in reversed(recorded[0])
        if message.metadata and CHAT_ERROR_KEY in message.metadata
    )

    assert isinstance(target, ModelRequest)
    assert target.metadata is not None
    # Internals stay server-side, the user sees the same generic error live.
    assert "provider exploded" not in body
    assert target.metadata[CHAT_ERROR_KEY] in body


async def test_interrupted_stream_persists_the_partial_answer() -> None:
    """The partial answer the user already saw is persisted with the prompt."""

    async def stream_partial(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "The answer "
        yield "is 42"
        raise RuntimeError("connection dropped mid-stream")

    recorded, _ = await _run_turn(
        _prompt(), model=FunctionModel(stream_function=stream_partial)
    )

    assert _texts(recorded[0]) == ["q1", "The answer is 42"]


async def test_cancelled_response_stops_the_model_stream_and_persists() -> None:
    """A cancel stops provider streaming and ends only once the partial output is saved."""
    started = asyncio.Event()
    never = asyncio.Event()
    writing = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def stream_until_cancelled(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "partial"
        started.set()

        try:
            await never.wait()
        finally:
            cancelled.set()

    recorded: list[list[ModelMessage]] = []

    async def persist(messages: Sequence[ModelMessage]) -> None:
        writing.set()
        await release.wait()
        recorded.append(list(messages))

    adapter = _adapter(
        _prompt(), model=FunctionModel(stream_function=stream_until_cancelled)
    )
    response = _stream(adapter, persist)

    async def consume() -> None:
        async for _ in response.body_iterator:
            pass

    consumer = asyncio.create_task(consume())
    await started.wait()
    consumer.cancel()
    await writing.wait()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert cancelled.is_set()
    assert _texts(recorded[0]) == ["q1", "partial"]


async def test_reasoning_duration_is_persisted_as_message_metadata() -> None:
    """Reasoning timing stays UI metadata, not provider metadata."""
    clock = iter([10.0, 12.4])

    async def stream_reasoning(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaThinkingPart] | str]:
        yield {0: DeltaThinkingPart(content="weighing options")}
        yield "answer"

    recorded, body = await _run_turn(
        _prompt(),
        model=FunctionModel(stream_function=stream_reasoning),
        clock=lambda: next(clock),
    )
    (response,) = (msg for msg in recorded[0] if isinstance(msg, ModelResponse))
    durations = {response.run_id: [2400]}
    (reloaded,) = dump_transcript([response], None)

    assert response.metadata == {REASONING_DURATIONS_KEY: durations}
    assert "reasoningDurationsMs" in body
    assert isinstance(reloaded.metadata, dict)
    assert reloaded.metadata[REASONING_DURATIONS_KEY] == durations
    assert "pydantic_ai" in reloaded.metadata


async def test_persist_failure_hard_fails_with_an_error_chunk() -> None:
    """A failed write surfaces a trailing error chunk on a clean drain."""

    async def failing(messages: Sequence[ModelMessage]) -> None:
        raise RuntimeError("db down")

    _, body = await _run_turn(_prompt(), persist=failing)

    assert "Failed to save the conversation" in body


def test_an_export_imports_as_the_messages_it_stores() -> None:
    """A compaction loads back as a request, and a call left awaiting is closed."""
    call = ToolCallPart(tool_name="write", args={}, tool_call_id="c")
    stored = [
        *_ANSWERED,
        _COMPACTION,
        ModelRequest(parts=[UserPromptPart(content="q2")]),
        ModelResponse(parts=[call], model_name="m"),
    ]

    loaded = load_archived(dump_transcript(stored, pending_requests(stored)))

    assert [type(message) for message in loaded] == [
        ModelRequest,
        ModelResponse,
        ModelResponse,
        ModelRequest,
        ModelResponse,
        ModelRequest,
    ]
    assert is_compaction(loaded[2])
    assert is_user_request(loaded[3])
    assert [part.tool_call_id for part in _parts(loaded[-1:], ToolReturnPart)] == ["c"]


async def test_import_conversation_rejects_empty_export() -> None:
    """The guard runs ahead of any database write."""
    with pytest.raises(ValueError, match="no messages"):
        await import_conversation(owned("conversation"), [])


@pytest.mark.parametrize(
    ("instructions", "expected"),
    [
        (["SYS", "SYS", "SYS"], [("SYS", ["n0", "n1", "n2"])]),
        (
            ["SYS", "SYS scoped", "SYS"],
            [("SYS", ["n0"]), ("SYS scoped", ["n1"]), ("SYS", ["n2"])],
        ),
        ([None], []),
    ],
    ids=["unchanged-collapses", "changed-starts-new", "none-skipped"],
)
def test_an_export_reads_its_system_prompts_from_the_history(
    instructions: list[str | None], expected: list[tuple[str, list[str]]]
) -> None:
    """A prompt that never changed is one snapshot, and a turn without one, or a response, adds none."""
    messages: list[ModelMessage] = [ModelResponse(parts=[TextPart(content="a")])]

    for i, text in enumerate(instructions):
        request = ModelRequest(
            parts=[UserPromptPart(content=f"q{i}")], instructions=text
        )
        stamp([request], f"n{i}")
        messages.append(request)

    snapshots = instruction_snapshots(messages)

    assert [(s.text, s.message_ids) for s in snapshots] == expected


def _server(text: str | None) -> ServerConversation:
    messages = [] if text is None else _prompt(text)

    return ServerConversation(id="c1", title="persisted", messages=messages)


def _client(text: str) -> ClientConversation:
    return ClientConversation(id="c1", title="in memory", messages=_prompt(text))


@pytest.mark.parametrize(
    ("backend", "restored", "title"),
    [
        (_server("persisted"), "persisted", "persisted"),
        (None, "live", "in memory"),
        (_server(None), "live", "in memory"),
    ],
    ids=["persisted-wins", "draft", "never-persisted"],
)
def test_an_archive_restores_the_persisted_half_else_the_client_one(
    backend: ServerConversation | None, restored: str, title: str
) -> None:
    """The server copy is authoritative whenever it has messages, a draft or an errored turn has only the client's."""
    archive = ConversationArchive(backend=backend, frontend=_client("live"))
    messages, name = archive.active_path()

    assert _texts(ChatAdapter.load_messages(messages)) == [restored]
    assert name == title


def test_an_archive_names_itself_by_the_other_half_and_may_be_empty() -> None:
    nameless = ServerConversation(messages=_prompt("q"))

    assert (
        ConversationArchive(backend=nameless, frontend=_client("x")).active_path()[1]
        == "in memory"
    )
    assert ConversationArchive().active_path() == ([], None)


async def test_an_attached_url_is_rejected() -> None:
    """An import admits inline images alone, so no URL is ever fetched for it."""
    part = FileUIPart(
        url="http://127.0.0.1:5432/secret.pdf",
        media_type="application/pdf",
        provider_metadata={"pydantic_ai": {"force_download": "allow-local"}},
    )
    messages = load_archived(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q"), part])]
    )

    with pytest.raises(HTTPException):
        await accept_imported(messages)


async def test_an_import_is_held_to_the_caps_of_a_run(
    configure: Callable[..., None],
) -> None:
    """A tool result is cut as a run cuts one, and no message carries more images than the gateway takes."""
    configure(multimodal={"max_images": 1})
    cap = settings.llm.tool_output_max_chars
    returned = ToolReturnPart("grep", "x" * (cap + 400), "c1")
    image = BinaryContent(b"png", media_type="image/png")

    await accept_imported([ModelRequest(parts=[returned])])

    assert isinstance(returned.content, str) and len(returned.content) == cap
    assert returned.content.endswith("characters.]")

    with pytest.raises(HTTPException):
        await accept_imported(
            [ModelRequest(parts=[UserPromptPart(["q", image, image])])]
        )


def _stamped(*turns: tuple[str, ModelMessage]) -> list[ModelMessage]:
    """*turns* as a history stores them, each message under its UI message id."""
    for ui_message_id, message in turns:
        stamp([message], ui_message_id)

    return [message for _, message in turns]


def _history() -> list[ModelMessage]:
    """A two-turn history: user, answer, user, answer."""
    return _stamped(
        ("q1", ModelRequest(parts=[UserPromptPart(content="q1")])),
        ("a1", ModelResponse(parts=[TextPart(content="a1")])),
        ("q2", ModelRequest(parts=[UserPromptPart(content="q2")])),
        ("a2", ModelResponse(parts=[TextPart(content="a2")])),
    )


@pytest.mark.parametrize(
    ("target", "regenerate", "kept"),
    [
        ("q2", False, ["q1", "a1"]),
        ("a2", True, ["q1", "a1", "q2"]),
        ("q2", True, ["q1", "a1", "q2"]),
        ("local-42", True, ["q1", "a1", "q2"]),
        (None, True, ["q1", "a1", "q2"]),
        ("a2", False, None),
        ("local-42", False, None),
    ],
)
def test_an_edit_or_a_regenerate_keeps_what_shows_before_the_replaced_message(
    target: str | None, regenerate: bool, kept: list[str] | None
) -> None:
    """A regenerate targets the reply, its prompt, or the last prompt for a reply the history lacks, and a message sent after a reply is no edit."""
    result = kept_before(_history(), target, regenerate=regenerate, reply_id="new")

    assert (None if result is None else _texts(result)) == kept


def _compacted(owner: str) -> list[ModelMessage]:
    """A two-turn history whose compaction is stored under the UI message *owner*."""
    return _stamped(
        ("q1", ModelRequest(parts=[UserPromptPart(content="q1")])),
        ("a1", ModelResponse(parts=[TextPart(content="a1")])),
        (owner, ModelResponse(parts=[CompactionPart(content="the gist")])),
        ("q2", ModelRequest(parts=[UserPromptPart(content="q2")])),
        ("a2", ModelResponse(parts=[TextPart(content="a2")])),
    )


@pytest.mark.parametrize(
    ("owner", "shown_in", "types"),
    [
        ("a2", 3, ["data-compaction", "step-start", "text"]),
        ("a1", 1, ["step-start", "text", "data-compaction"]),
    ],
    ids=["opens-its-reply", "of-a-finished-reply"],
)
def test_a_compaction_shows_in_the_reply_it_belongs_to(
    owner: str, shown_in: int, types: list[str]
) -> None:
    """A compaction a reply opens shows after its prompt, one the compact button made stays in the reply it compacted."""
    transcript = dump_transcript(_compacted(owner), None)

    assert [m.id for m in transcript] == ["q1", "a1", "q2", "a2"]
    assert [part.type for part in transcript[shown_in].parts] == types


def test_a_compaction_a_reply_opens_stays_for_the_reply_replacing_it() -> None:
    history = _compacted("a2")
    kept = kept_before(history, "a2", regenerate=True, reply_id="new")

    assert kept is not None and _texts(kept) == ["q1", "a1", "q2"]
    assert [ui_id(m) for m in kept if is_compaction(m)] == ["new"]
    assert ui_id(history[2]) == "a2"


def test_a_turn_is_one_message_under_its_ui_id() -> None:
    """Every response of a reply folds into one message, each opening a step, its metadata merged."""
    delta = ModelSettingsDeltaPart(settings={"thinking": "medium"}, top_level=True)
    history = _stamped(
        ("q", ModelRequest(parts=[delta, UserPromptPart(content="q")])),
        (
            "r",
            ModelResponse(
                parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")],
                metadata={REASONING_DURATIONS_KEY: {"r1": [1000]}},
            ),
        ),
        (
            "r",
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="t",
                        content="r",
                        tool_call_id="c1",
                        metadata=DataChunk(type="data-undeclared", data={}),
                    ),
                    delta,
                    RetryFeedbackPart("Answer.", retry=RetryInfo(cause="no_output")),
                ]
            ),
        ),
        (
            "r",
            ModelResponse(
                parts=[TextPart(content="answer")],
                metadata={REASONING_DURATIONS_KEY: {"r2": [2400]}},
            ),
        ),
    )

    ui = dump_transcript(history, None)

    assert [msg.id for msg in ui] == ["q", "r"]
    assert [part.type for part in ui[0].parts] == ["text"]
    assert [part.type for part in ui[1].parts] == [
        "step-start",
        "tool-t",
        "step-start",
        "text",
    ]
    # Metadata merges as the client merges a continuation's.
    assert isinstance(ui[1].metadata, dict)
    assert ui[1].metadata[REASONING_DURATIONS_KEY] == {"r1": [1000], "r2": [2400]}


async def test_a_compaction_streams_as_the_part_a_reload_shows() -> None:
    """A run's compaction reaches the client as it reloads."""
    part = CompactionPart(content="the gist")
    reloaded = dump_transcript([ModelResponse(parts=[part])], None)
    shown = reloaded[0].parts[-1].model_dump(mode="json", by_alias=True)

    live = [
        chunk.model_dump(mode="json", by_alias=True, exclude_none=True)
        async for chunk in _adapter([])
        .build_event_stream()
        .handle_capability_event(Compacted(part=part))
    ]

    assert shown == {"type": "data-compaction", "data": {"content": "the gist"}}
    assert live == [shown]


async def test_only_declared_data_parts_stream() -> None:
    """A data part the client does not declare stays server side, as on reload."""

    async def chunks() -> AsyncIterator[BaseChunk]:
        yield DataChunk(type="data-model-settings-delta", data={})
        yield DataChunk(type="data-compaction", data={"content": "gist"})

    body = "".join(
        [str(chunk) async for chunk in stream_response(chunks()).body_iterator]
    )

    assert "data-compaction" in body
    assert "data-model-settings-delta" not in body
