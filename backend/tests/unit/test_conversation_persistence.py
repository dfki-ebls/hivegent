"""Tests for the stateless surface of chat persistence.

A turn loads its conversation from the store, runs on its history, and saves
the turn's messages after it.  These exercise the parts that need no
database: ``run_and_persist`` (what the persist callback receives on clean,
errored, and interrupted finishes, and the hard-fail error chunk),
``kept_before`` (what an edit or a regenerate runs on), and
``dump_transcript`` (one ``UIMessage`` per UI message id the stored messages carry).
"""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from types import SimpleNamespace

import pytest
from exar.agents import RunConfig
from exar.policy import allow_all
from exar.pydantic_ai import traced_events
from exar.pydantic_ai.compaction import Compacted, is_compaction
from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai import Agent, BinaryContent, DeferredToolRequests, RunContext
from pydantic_ai.conversation import Conversation as RunConversation
from pydantic_ai.conversation_store import ConversationRecord
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

import hivegent.server.vercel.adapter as vercel_module
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
    store,
)
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


def pending_requests(messages: Sequence[ModelMessage]) -> DeferredToolRequests | None:
    """The calls the last response of *messages* awaits, as a paused run's record keeps them.

    A question awaits an answer, any other call an approval.
    """
    answered = {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
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


def _parts[T](messages: Sequence[ModelMessage], kind: type[T]) -> list[T]:
    """Every part of *kind* across *messages*, in order."""
    return [
        part for message in messages for part in message.parts if isinstance(part, kind)
    ]


def _tool_states(messages: Sequence[ModelMessage]) -> list[str | None]:
    """Reload states of the tool cards a message list projects to."""
    return [
        getattr(part, "state", None)
        for message in _ui_messages(messages)
        for part in message.parts
        if str(getattr(part, "type", "")).startswith("tool-")
    ]


def _tool_approvals(
    messages: Sequence[ModelMessage],
) -> list[tuple[bool | None, str | None]]:
    """Approval decisions and notes the tool cards a message list projects to carry."""
    return [
        (getattr(approval, "approved", None), getattr(approval, "reason", None))
        for message in _ui_messages(messages)
        for part in message.parts
        if str(getattr(part, "type", "")).startswith("tool-")
        for approval in [getattr(part, "approval", None)]
    ]


def _adapter(
    ui_messages: list[UIMessage],
    *,
    model: Model | None = None,
    tool_raises: bool = False,
    tool_needs_approval: bool = False,
    tool_runs: bool = False,
    asks_user: bool = False,
    regenerate: bool = False,
) -> ChatAdapter[None, str | DeferredToolRequests]:
    output_type: OutputSpec[str | DeferredToolRequests] = (
        [str, DeferredToolRequests] if tool_needs_approval or asks_user else str
    )
    agent = Agent[None, str | DeferredToolRequests](
        model=model or TestModel(custom_output_text="ANSWER"),
        output_type=output_type,
        deps_type=type(None),
        capabilities=[ApprovalNotes()],
    )

    if asks_user:
        agent.tool_plain(ask_user)

    if tool_raises:

        @agent.tool_plain
        def boom() -> str:
            raise RuntimeError("simulated tool failure mid-run")

    if tool_runs:

        @agent.tool_plain
        def lookup() -> str:
            return "found"

    if tool_needs_approval:

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
        if regenerate
        else SubmitMessage(id="c1", messages=ui_messages, trigger="submit-message"),
        sdk_version=SDK_VERSION,
    )


async def _run_turn(
    ui_messages: list[UIMessage],
    *,
    model: Model | None = None,
    message_history: Sequence[ModelMessage] | None = None,
    tool_raises: bool = False,
    tool_needs_approval: bool = False,
    tool_runs: bool = False,
    asks_user: bool = False,
    regenerate: bool = False,
    persist: PersistTurn | None = None,
) -> tuple[list[list[ModelMessage]], str]:
    """Run one turn through ``run_and_persist``; return recorded turns + body."""
    adapter = _adapter(
        ui_messages,
        model=model,
        tool_raises=tool_raises,
        tool_needs_approval=tool_needs_approval,
        tool_runs=tool_runs,
        asks_user=asks_user,
        regenerate=regenerate,
    )
    history = list(message_history or [])
    recorded: list[list[ModelMessage]] = []

    async def _record(messages: Sequence[ModelMessage]) -> None:
        recorded.append(list(messages))

    response = stream_response(
        run_and_persist(
            adapter,
            adapter.transform_stream(
                traced_events(
                    agent_events(
                        adapter.agent.run_stream_events(
                            adapter.prompt,
                            message_history=history,
                            deferred_tool_results=adapter.resume(
                                pending_requests(history), history
                            ),
                        )
                    ),
                    "q",
                    RunConfig(policy=allow_all),
                    name="chat",
                )
            ),
            persist=persist or _record,
        ),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(
        chunk.decode() if isinstance(chunk, bytes) else str(chunk) for chunk in chunks
    )
    return recorded, body


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

    adapter = _adapter([UIMessage(id="m1", role="user", parts=[TextUIPart(text="q")])])
    chunks = run_and_persist(
        adapter, adapter.transform_stream(events()), persist=persist
    )
    assert isinstance(chunks, AsyncGenerator)

    while not log:
        _ = await anext(chunks)

    await chunks.aclose()

    assert log == ["started", "closed", "persisted"]


async def test_clean_turn_persists_only_the_new_tail() -> None:
    """A turn appends only its new messages past the replayed history prefix."""
    prefix: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="q1")]),
        ModelResponse(parts=[TextPart(content="a1")]),
    ]
    recorded, _ = await _run_turn(
        [UIMessage(id="m2", role="user", parts=[TextUIPart(text="q2")])],
        message_history=prefix,
    )

    assert len(recorded) == 1
    delta = recorded[0][len(prefix) :]
    texts = _texts(delta)
    assert "q2" in texts
    assert "ANSWER" in texts
    assert "q1" not in texts  # the prefix is replayed, not re-persisted


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
        "after-compaction",
        "past-compaction",
        "after-closed-call",
        "after-failed-prompt",
    ],
)
async def test_a_prompt_merged_into_the_replay_is_its_own_node(
    prefix: list[ModelMessage],
) -> None:
    """The run merges consecutive requests, so the prompt can sit in a replayed one."""
    recorded, _ = await _run_turn(
        [UIMessage(id="m2", role="user", parts=[TextUIPart(text="q2")])],
        message_history=prefix,
    )
    turn = Replay.of(prefix).turn(recorded[0])

    assert turn.above is None
    assert _texts(turn.messages) == ["q2", "ANSWER"]
    assert not _parts(turn.messages, CompactionPart)
    assert not _parts(turn.messages, ToolReturnPart)


async def test_regenerate_reruns_from_history_without_a_new_message() -> None:
    """Regenerate runs on a prefix ending in a user request with no client message.

    The frontend sends ``messages: []`` for a regenerate; the agent must still
    produce a response from the replayed history, and the delta is just that
    response.
    """
    prefix: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="q1")])]
    recorded, _ = await _run_turn([], message_history=prefix, regenerate=True)

    delta = recorded[0][len(prefix) :]
    assert any(isinstance(msg, ModelResponse) for msg in delta)
    assert "ANSWER" in _texts(delta)


async def test_errored_turn_closes_its_dangling_tool_call() -> None:
    """A turn whose tool raises persists a failed return closing the orphan call.

    The raised tool aborts the run, leaving a ``ToolCallPart`` with no return in
    ``capture_run_messages``.  Persisting that verbatim would make the reload
    projection read it as an approval request and the history invalid to replay,
    so ``run_and_persist`` closes it with the failed return the stream closed
    it with, so the tool card reloads as failed, as it streamed, not as an
    approval prompt.
    """
    recorded, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_raises=True,
    )
    messages = recorded[0]

    assert "q1" in _texts(messages)
    call = _parts(messages, ToolCallPart)[0]
    ret = next(
        part
        for part in _parts(messages, ToolReturnPart)
        if part.tool_call_id == call.tool_call_id
    )
    assert ret.outcome == "failed"
    assert "simulated tool failure" not in str(ret.content)
    assert _tool_states(messages) == ["output-error"]


async def test_approval_pending_turn_keeps_its_dangling_call() -> None:
    """A clean finish awaiting approval leaves its call open, not error-closed.

    An approval-required tool ends the run cleanly with ``DeferredToolRequests``,
    leaving a ``ToolCallPart`` with no return, the same shape as an error
    orphan.  Since no error streamed, the turn is persisted untouched so it
    reloads as an approval request, not a failed tool.
    """
    recorded, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    messages = recorded[0]

    assert not _parts(messages, ToolReturnPart)
    assert _tool_states(messages) == ["approval-requested"]


async def test_approval_metadata_reaches_the_stream_and_the_reload() -> None:
    """What an ``ApprovalRequired`` carried is shown live and after a reload alike."""
    recorded, body = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    call = _parts(recorded[0], ToolCallPart)[0]
    expected = {call.tool_call_id: {"value": call.args_as_dict()["value"]}}

    streamed = [
        chunk["messageMetadata"]
        for chunk in sse_chunks(body)
        if chunk["type"] == "message-metadata"
    ]
    reloaded = _ui_messages(recorded[0])[-1].metadata

    assert streamed[-1][APPROVAL_METADATA_KEY] == expected
    assert isinstance(reloaded, dict)
    assert reloaded[APPROVAL_METADATA_KEY] == expected


def _answer_approval(
    pending: Sequence[ModelMessage], *, approved: bool, note: str | None = None
) -> UIMessage:
    """Echo the assistant message holding the pending call, approval answered.

    Exactly what the AI SDK re-sends when it auto-continues: the last message,
    with its tool part flipped to ``approval-responded``.
    """
    message = _ui_messages(pending)[-1]
    message.parts = [
        ToolApprovalRespondedPart(
            type=part.type,
            tool_call_id=part.tool_call_id,
            input=part.input,
            approval=ToolApprovalResponded(
                id=part.tool_call_id,
                approved=approved,
                reason=note,
            ),
        )
        for part in message.parts
        if isinstance(part, ToolApprovalRequestedPart)
    ]
    return message


@pytest.mark.parametrize("approved", [True, False])
async def test_answered_approval_resolves_the_stored_call_once(
    approved: bool,
) -> None:
    """The continuation resolves the stored call instead of replaying it.

    Replaying the client echo on top of the server-side prefix would duplicate
    the ``tool_call_id`` and loop the run (see ``backend/README.md``).
    """
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    recorded, _ = await _run_turn(
        [_answer_approval(pending[0], approved=approved)],
        message_history=pending[0],
        tool_needs_approval=True,
    )
    messages = recorded[0]

    calls = _parts(messages, ToolCallPart)
    returns = _parts(messages, ToolReturnPart)

    assert len(calls) == 1
    assert len(returns) == 1
    assert returns[0].tool_call_id == calls[0].tool_call_id
    assert str(returns[0].content) == ("written" if approved else denial_message(None))


@pytest.mark.parametrize("approved", [True, False])
async def test_a_decision_note_reaches_the_model_and_the_reload(approved: bool) -> None:
    """The user's note on either decision is in the tool result the model reads.

    A denial carries it in the refusal, an approval appends it to the result of
    the call it released.  The reloaded card shows the note as typed, not the
    model-facing text around it.
    """
    note = "Keep a copy of the old version."
    seen: list[ModelMessage] = []

    async def respond(
        messages: Sequence[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        seen.extend(messages)
        yield "ANSWER"

    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    recorded, _ = await _run_turn(
        [_answer_approval(pending[0], approved=approved, note=note)],
        model=FunctionModel(stream_function=respond),
        message_history=pending[0],
        tool_needs_approval=True,
    )

    expected = (
        ["written", approval_note_text(note)] if approved else denial_message(note)
    )
    assert [part.content for part in _parts(seen, ToolReturnPart)] == [expected]
    assert _tool_approvals(recorded[0]) == [(approved, note)]


@pytest.mark.parametrize("approved", [True, False])
async def test_a_settled_approval_reloads_with_its_decision(approved: bool) -> None:
    """Both decisions survive a reload, not just the denial.

    A denial is self-describing: its return carries ``outcome='denied'`` and the
    reason, so it projects straight back.  An approved call runs and stores an
    ordinary successful return, so nothing in the message list says it was ever
    gated and the decision has to be recorded at persist time for the
    projection to put it back.
    """
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    recorded, _ = await _run_turn(
        [_answer_approval(pending[0], approved=approved)],
        message_history=pending[0],
        tool_needs_approval=True,
    )

    assert _tool_states(recorded[0]) == [
        "output-available" if approved else "output-denied"
    ]
    assert _tool_approvals(recorded[0]) == [(approved, None)]


async def test_abandoned_approval_is_declined_for_the_next_turn() -> None:
    """A prompt sent instead of an answer closes the pending call as a denial.

    Left dangling, the call is repaired into a generic "interrupted" result on
    the way to the provider on every later turn, and the model reads that as a
    transient failure and reissues the identical call each time.
    """
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    declined = abandon_pending_calls(pending_requests(pending[0]))
    assert declined is not None

    returns = _parts([declined], ToolReturnPart)
    assert [r.outcome for r in returns] == ["denied"]
    assert returns[0].tool_call_id == _parts(pending[0], ToolCallPart)[0].tool_call_id
    # The card reloads as a refusal rather than asking for approval all over
    # again, with no note, since nobody typed one.
    assert _tool_states([*pending[0], declined]) == ["output-denied"]
    assert _tool_approvals([*pending[0], declined]) == [(False, None)]


async def test_a_settled_turn_has_no_approval_to_decline() -> None:
    """Only a turn left awaiting approval is declined, never a finished one."""
    settled, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])]
    )

    assert abandon_pending_calls(pending_requests(settled[0])) is None
    assert abandon_pending_calls(pending_requests([])) is None


@pytest.mark.parametrize("stopped", [False, True])
async def test_a_call_a_stop_closed_is_no_longer_pending(
    monkeypatch: pytest.MonkeyPatch, stopped: bool
) -> None:
    """A stop after a run paused closes its call, so no later request resolves it again."""
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    requests = pending_requests(pending[0])
    messages = list(pending[0])
    written: list[ConversationRecord] = []

    async def read_record(_id: str) -> None:
        return None

    async def write_record(record: ConversationRecord, **_kwargs: object) -> None:
        written.append(record)

    monkeypatch.setattr(store, "read_record", read_record)
    monkeypatch.setattr(store, "write_record", write_record)

    if stopped:
        messages = close_orphan_tool_calls(
            messages, INTERRUPTED_TOOL_ERROR, "interrupted"
        )

    await store.save(
        RunConversation(messages=messages, deferred_tool_requests=requests), scope="u"
    )

    assert requests is not None
    assert written[0].deferred_tool_requests == (None if stopped else requests)


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
    recorded, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        model=_asking_model([]),
        asks_user=True,
    )
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


async def test_a_pending_question_reloads_awaiting_its_answer() -> None:
    """A deferred question stays open and reloads as a question, not an approval."""
    pending = await _pending_question()

    assert not _parts(pending, ToolReturnPart)
    assert _tool_states(pending) == ["input-available"]


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


async def test_an_invalid_answer_is_rejected() -> None:
    """The client writes the answer, so one that answers nothing never reaches the model."""
    pending = await _pending_question()
    adapter = _adapter([_reply(pending, [{"selected": []}])], asks_user=True)

    with pytest.raises(ValidationError):
        adapter.resume(pending_requests(pending), pending)


async def test_a_decision_for_a_call_no_longer_pending_is_refused() -> None:
    """A client behind the store is told so, rather than its decision being dropped."""
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    adapter = _adapter(
        [_answer_approval(pending[0], approved=True)], tool_needs_approval=True
    )

    with pytest.raises(StaleAnswers):
        adapter.resume(None, pending[0])


async def test_an_approval_is_bound_to_the_changes_it_was_asked_with() -> None:
    """The gate replans an approved write and refuses it once the changes differ."""
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    requests = pending_requests(pending[0])
    assert requests is not None
    call_id = requests.approvals[0].tool_call_id
    *_, asked = pending[0]
    assert isinstance(asked, ModelResponse)
    asked.metadata = {
        APPROVAL_METADATA_KEY: {call_id: {"items": [{"path": "~/a.md"}], "added": 1}}
    }
    adapter = _adapter(
        [_answer_approval(pending[0], approved=True)], tool_needs_approval=True
    )
    results = adapter.resume(requests, pending[0])

    assert results is not None
    assert results.metadata[call_id][APPROVED_CHANGES] == changes_digest(
        {"added": 1, "items": [{"path": "~/a.md"}]}
    )


async def test_an_approval_answered_like_a_question_is_rejected() -> None:
    """Each pending call takes only the kind of result it was deferred for."""
    pending, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_needs_approval=True,
    )
    message = _ui_messages(pending[0])[-1]
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
        adapter.resume(pending_requests(pending[0]), pending[0])


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
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
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


async def test_an_abandoned_question_is_dismissed() -> None:
    """A prompt sent instead of answers closes the question as dismissed, not denied."""
    pending = await _pending_question()
    closed = abandon_pending_calls(pending_requests(pending))
    assert closed is not None

    assert [part.content for part in _parts([closed], ToolReturnPart)] == [
        DISMISSED_QUESTIONS
    ]
    assert _tool_states([*pending, closed]) == ["output-error"]
    assert _tool_approvals([*pending, closed]) == [(None, None)]


async def test_generic_run_error_is_recorded_for_reload() -> None:
    """A run that fails before responding records the error on the user turn.

    The stream error is transient, so without this the banner is lost on reload.
    With no assistant response to attach to, the error rides on the last
    projecting message, the user prompt, under ``CHAT_ERROR_KEY``.
    """

    async def fail_immediately(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        raise RuntimeError("provider exploded")
        yield ""  # unreachable; marks this a stream function

    recorded, body = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        model=FunctionModel(stream_function=fail_immediately),
    )
    # The failed run still appends its own empty, interrupted ModelResponse, so
    # the error rides on the last message that *projects* to a UIMessage rather
    # than on the last message outright.
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
    """An interrupted turn persists its prompt and the in-flight partial.

    pydantic-ai v2 captures a partial response into ``capture_run_messages``
    before the error propagates, so this path persists the partial answer the
    user already saw rather than dropping it.
    """

    async def stream_partial(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "The answer "
        yield "is 42"
        raise RuntimeError("connection dropped mid-stream")

    recorded, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        model=FunctionModel(stream_function=stream_partial),
    )

    assert "q1" in _texts(recorded[0])
    assert "The answer is 42" in _texts(recorded[0])


async def test_cancelled_response_stops_the_model_stream_and_persists() -> None:
    """A cancel stops provider streaming and ends only once the partial output is saved."""
    started = asyncio.Event()
    cancelled = asyncio.Event()
    never = asyncio.Event()
    writing = asyncio.Event()
    release = asyncio.Event()

    async def stream_until_cancelled(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "partial"
        started.set()
        try:
            await never.wait()
        finally:
            cancelled.set()

    adapter = _adapter(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        model=FunctionModel(stream_function=stream_until_cancelled),
    )
    recorded: list[list[ModelMessage]] = []

    async def persist(messages: Sequence[ModelMessage]) -> None:
        writing.set()
        _ = await release.wait()
        recorded.append(list(messages))

    response = stream_response(
        run_and_persist(
            adapter,
            adapter.transform_stream(
                traced_events(
                    agent_events(adapter.agent.run_stream_events(adapter.prompt)),
                    "q1",
                    RunConfig(policy=allow_all),
                    name="chat",
                )
            ),
            persist=persist,
        ),
    )

    async def consume() -> None:
        async for _ in response.body_iterator:
            pass

    consumer = asyncio.create_task(consume())
    await started.wait()
    consumer.cancel()
    _ = await writing.wait()
    # A second cancel, as a stop during the write sends, still waits for it.
    consumer.cancel()
    await asyncio.sleep(0)
    assert not consumer.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert cancelled.is_set()
    assert recorded
    assert "partial" in _texts(recorded[0])


async def test_reasoning_duration_is_persisted_as_message_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reasoning timing stays UI metadata, not provider metadata."""
    ticks = iter([10.0, 12.4])
    monkeypatch.setattr(
        vercel_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )

    async def stream_reasoning(
        messages: Sequence[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaThinkingPart] | str]:
        yield {0: DeltaThinkingPart(content="weighing options")}
        yield "answer"

    recorded, body = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        model=FunctionModel(stream_function=stream_reasoning),
    )

    response = next(msg for msg in recorded[0] if isinstance(msg, ModelResponse))

    durations = {response.run_id: [2400]}

    assert response.metadata == {REASONING_DURATIONS_KEY: durations}
    assert "reasoningDurationsMs" in body

    ui = dump_transcript([response], None)

    assert isinstance(ui[0].metadata, dict)
    assert ui[0].metadata[REASONING_DURATIONS_KEY] == durations
    assert "pydantic_ai" in ui[0].metadata


async def test_persist_failure_hard_fails_with_an_error_chunk() -> None:
    """A failed write surfaces a trailing error chunk on a clean drain."""

    async def failing(messages: Sequence[ModelMessage]) -> None:
        raise RuntimeError("db down")

    _, body = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        persist=failing,
    )

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
    shown = dump_transcript(stored, pending_requests(stored))

    loaded = load_archived(shown)

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
    """Importing a message-less export fails before any database write.

    The guard runs ahead of persistence, so it is exercised here without a
    live database; the happy path is DB-backed and covered by smoke tests.
    """
    with pytest.raises(ValueError, match="no messages"):
        await import_conversation(owned("conversation"), [])


class TestInstructionSnapshots:
    """The system prompts an export reads back out of the stored history."""

    def _pairs(self, *instructions: str | None) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        for i, text in enumerate(instructions):
            request = ModelRequest(
                parts=[UserPromptPart(content=f"q{i}")], instructions=text
            )
            stamp([request], f"n{i}")
            messages.append(request)
        return messages

    def test_consecutive_identical_prompts_collapse(self) -> None:
        """A prompt that never changed is one snapshot, not one per turn."""
        snapshots = instruction_snapshots(self._pairs("SYS", "SYS", "SYS"))

        assert len(snapshots) == 1
        assert snapshots[0].message_ids == ["n0", "n1", "n2"]

    def test_a_changed_prompt_starts_a_new_snapshot(self) -> None:
        """Narrowing the document scope mid-conversation stays visible."""
        snapshots = instruction_snapshots(self._pairs("SYS", "SYS scoped", "SYS"))

        assert [s.text for s in snapshots] == ["SYS", "SYS scoped", "SYS"]
        assert [s.message_ids for s in snapshots] == [["n0"], ["n1"], ["n2"]]

    def test_messages_carrying_no_prompt_are_skipped(self) -> None:
        """Responses hold none, and an imported turn may have lost its own."""
        messages: list[ModelMessage] = [
            ModelResponse(parts=[TextPart(content="a")]),
            *self._pairs(None),
        ]
        assert instruction_snapshots(messages) == []


class TestConversationArchive:
    """Which half of an exported archive an import restores."""

    def _server(self, text: str | None) -> ServerConversation:
        messages = (
            [UIMessage(id="m1", role="user", parts=[TextUIPart(text=text)])]
            if text is not None
            else []
        )
        return ServerConversation(id="c1", title="persisted", messages=messages)

    def _client(self, text: str) -> ClientConversation:
        return ClientConversation(
            id="c1",
            title="in memory",
            messages=[UIMessage(id="m1", role="user", parts=[TextUIPart(text=text)])],
        )

    def test_persisted_half_wins(self) -> None:
        """The server copy is authoritative whenever it has messages."""
        archive = ConversationArchive(
            backend=self._server("persisted"), frontend=self._client("in memory")
        )
        messages, title = archive.active_path()

        assert _texts(ChatAdapter.load_messages(messages)) == ["persisted"]
        assert title == "persisted"

    def test_client_half_covers_a_turn_that_never_persisted(self) -> None:
        """A draft, or a turn that errored before the write, has only this half."""
        for backend in (None, self._server(None)):
            archive = ConversationArchive(
                backend=backend, frontend=self._client("live")
            )
            messages, title = archive.active_path()

            assert _texts(ChatAdapter.load_messages(messages)) == ["live"]
            assert title == "in memory"

    def test_title_falls_back_to_the_other_half(self) -> None:
        """A server copy titled by neither side still imports under a name."""
        archive = ConversationArchive(
            backend=ServerConversation(
                messages=[UIMessage(id="m1", role="user", parts=[TextUIPart(text="q")])]
            ),
            frontend=self._client("live"),
        )
        assert archive.active_path()[1] == "in memory"

    def test_an_empty_archive_restores_nothing(self) -> None:
        """Neither half present is rejected by the import guard, not persisted."""
        assert ConversationArchive().active_path() == ([], None)

    async def test_an_attached_url_is_rejected(self) -> None:
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
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tool result is cut as a run cuts one, and no message carries more images than the gateway takes."""
        monkeypatch.setattr(settings.llm, "tool_output_max_chars", 100)
        monkeypatch.setattr(settings.multimodal, "max_images", 1)
        returned = ToolReturnPart("grep", "x" * 500, "c1")
        image = BinaryContent(b"png", media_type="image/png")

        await accept_imported([ModelRequest(parts=[returned])])

        assert isinstance(returned.content, str) and len(returned.content) == 100
        assert returned.content.endswith("[Cut short of its 500 characters.]")

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


def test_an_edit_keeps_what_shows_before_the_edited_message() -> None:
    kept = kept_before(_history(), "q2", regenerate=False, reply_id="new")

    assert kept is not None and _texts(kept) == ["q1", "a1"]


def test_a_regenerate_keeps_the_prompt_of_the_reply() -> None:
    """The reply or its prompt is the target, or the last prompt for a reply the history lacks."""
    for target in ["a2", "q2", "local-42", None]:
        kept = kept_before(_history(), target, regenerate=True, reply_id="new")

        assert kept is not None and _texts(kept) == ["q1", "a1", "q2"]


def test_a_message_sent_after_a_reply_is_no_edit() -> None:
    for target in ["a2", "local-42"]:
        assert kept_before(_history(), target, regenerate=False, reply_id="new") is None


def test_a_reply_a_compaction_opens_shows_it_after_its_prompt() -> None:
    """A compaction stored ahead of its prompt shows in the reply, and stays for the reply that replaces it."""
    history = _stamped(
        ("q1", ModelRequest(parts=[UserPromptPart(content="q1")])),
        ("a1", ModelResponse(parts=[TextPart(content="a1")])),
        ("a2", ModelResponse(parts=[CompactionPart(content="the gist")])),
        ("q2", ModelRequest(parts=[UserPromptPart(content="q2")])),
        ("a2", ModelResponse(parts=[TextPart(content="a2")])),
    )

    *_, prompt, reply = dump_transcript(history, None)

    assert (prompt.id, reply.id) == ("q2", "a2")
    assert [part.type for part in reply.parts] == [
        "data-compaction",
        "step-start",
        "text",
    ]

    kept = kept_before(history, "a2", regenerate=True, reply_id="new")

    assert kept is not None and _texts(kept) == ["q1", "a1", "q2"]
    assert [ui_id(m) for m in kept if is_compaction(m)] == ["new"]
    assert ui_id(history[2]) == "a2"


def test_a_compaction_of_a_finished_reply_stays_in_it() -> None:
    """The compact button's summary belongs to the reply it compacted, whatever prompt follows."""
    history = _stamped(
        ("q1", ModelRequest(parts=[UserPromptPart(content="q1")])),
        ("a1", ModelResponse(parts=[TextPart(content="a1")])),
        ("a1", ModelResponse(parts=[CompactionPart(content="the gist")])),
        ("q2", ModelRequest(parts=[UserPromptPart(content="q2")])),
        ("a2", ModelResponse(parts=[TextPart(content="a2")])),
    )

    transcript = dump_transcript(history, None)

    assert [m.id for m in transcript] == ["q1", "a1", "q2", "a2"]
    assert [part.type for part in transcript[1].parts] == [
        "step-start",
        "text",
        "data-compaction",
    ]


def test_a_turn_is_one_message_under_its_ui_id() -> None:
    """Every response of a reply folds into one message, each opening a step, its metadata merged."""
    history = _stamped(
        (
            "q",
            ModelRequest(
                parts=[
                    ModelSettingsDeltaPart(
                        settings={"thinking": "medium"}, top_level=True
                    ),
                    UserPromptPart(content="q"),
                ]
            ),
        ),
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
                    ModelSettingsDeltaPart(
                        settings={"thinking": "medium"}, top_level=True
                    ),
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

    answer = ui[1]
    assert [part.type for part in answer.parts] == [
        "step-start",
        "tool-t",
        "step-start",
        "text",
    ]
    # Metadata merges as the client merges a continuation's.
    assert isinstance(answer.metadata, dict)
    assert answer.metadata[REASONING_DURATIONS_KEY] == {"r1": [1000], "r2": [2400]}


async def test_a_compaction_streams_as_the_part_a_reload_shows() -> None:
    """A run's compaction reaches the client as it reloads."""
    adapter = _adapter([])
    part = CompactionPart(content="the gist")
    reloaded = dump_transcript([ModelResponse(parts=[part])], None)
    shown = reloaded[0].parts[-1].model_dump(mode="json", by_alias=True)

    live = [
        chunk.model_dump(mode="json", by_alias=True, exclude_none=True)
        async for chunk in adapter.build_event_stream().handle_capability_event(
            Compacted(part=part)
        )
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
