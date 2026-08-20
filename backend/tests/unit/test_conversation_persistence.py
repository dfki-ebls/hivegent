"""Tests for the stateless surface of DB-first chat persistence.

The conversation store is a server-authoritative message tree: each turn loads
the active-path prefix from SQL, runs the agent on it, and appends the turn's
new messages (``captured[len(prefix):]``) as a branch.  The tree writes
(``resolve_fork`` / ``append_branch``) touch PostgreSQL and are covered by
manual smoke tests; these exercise the parts that need no database:
``run_and_persist`` (what the persist callback receives on clean, errored, and
interrupted finishes, and the hard-fail error chunk) and
``dump_messages_with_ids`` (anchoring ``UIMessage`` ids to tree-node ids).
"""

from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.output import OutputSpec
from pydantic_ai.ui.vercel_ai.request_types import (
    SubmitMessage,
    TextUIPart,
    ToolApprovalRequestedPart,
    ToolApprovalResponded,
    ToolApprovalRespondedPart,
    UIMessage,
)
from starlette.responses import StreamingResponse

import hivegent.server.vercel as vercel_module
from hivegent.db.conversations import ActiveNode, _fork_for_path, import_conversation
from hivegent.server.routes.conversations import _instruction_snapshots
from hivegent.server.vercel import (
    CHAT_ERROR_KEY,
    REASONING_DURATIONS_KEY,
    SDK_VERSION,
    ChatAdapter,
    PersistTurn,
    decline_pending_approvals,
    dump_messages_with_ids,
    run_and_persist,
)
from hivegent.types import (
    ClientConversation,
    ConversationArchive,
    ServerConversation,
)

_TOOL_DENIED_REASON = "The user rejected this tool call. Do not retry it."


def _texts(messages: Sequence[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, (UserPromptPart, TextPart))
        and isinstance(part.content, str)
    ]


def _ui_messages(messages: Sequence[ModelMessage]) -> list[UIMessage]:
    """Project *messages* the way a reload does, with placeholder node ids."""
    return dump_messages_with_ids([(f"n{i}", m) for i, m in enumerate(messages)])


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


def _adapter(
    ui_messages: list[UIMessage],
    *,
    model: Model | None = None,
    tool_raises: bool = False,
    tool_needs_approval: bool = False,
) -> ChatAdapter[None, str | DeferredToolRequests]:
    output_type: OutputSpec[str | DeferredToolRequests] = (
        [str, DeferredToolRequests] if tool_needs_approval else str
    )
    agent = Agent[None, str | DeferredToolRequests](
        model=model or TestModel(custom_output_text="ANSWER"),
        output_type=output_type,
        deps_type=type(None),
    )

    if tool_raises:

        @agent.tool_plain
        def boom() -> str:
            raise RuntimeError("simulated tool failure mid-run")

    if tool_needs_approval:

        @agent.tool_plain(requires_approval=True)
        def write(value: str) -> str:
            return "written"

    return ChatAdapter[None, str | DeferredToolRequests](
        agent=agent,
        run_input=SubmitMessage(
            id="c1", messages=ui_messages, trigger="submit-message"
        ),
        sdk_version=SDK_VERSION,
    )


async def _run_turn(
    ui_messages: list[UIMessage],
    *,
    model: Model | None = None,
    message_history: Sequence[ModelMessage] | None = None,
    tool_raises: bool = False,
    tool_needs_approval: bool = False,
    persist: PersistTurn | None = None,
) -> tuple[list[list[ModelMessage]], str]:
    """Run one turn through ``run_and_persist``; return recorded turns + body."""
    adapter = _adapter(
        ui_messages,
        model=model,
        tool_raises=tool_raises,
        tool_needs_approval=tool_needs_approval,
    )
    recorded: list[list[ModelMessage]] = []

    async def _record(messages: Sequence[ModelMessage]) -> None:
        recorded.append(list(messages))

    response = await run_and_persist(
        adapter,
        adapter.run_stream(message_history=list(message_history or [])),
        persist=persist or _record,
    )
    assert isinstance(response, StreamingResponse)
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(
        chunk.decode() if isinstance(chunk, bytes) else str(chunk) for chunk in chunks
    )
    return recorded, body


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


async def test_regenerate_reruns_from_history_without_a_new_message() -> None:
    """Regenerate runs on a prefix ending in a user request with no client message.

    The frontend sends ``messages: []`` for a regenerate; the agent must still
    produce a response from the replayed history, and the delta is just that
    response.
    """
    prefix: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="q1")])]
    recorded, _ = await _run_turn([], message_history=prefix)

    delta = recorded[0][len(prefix) :]
    assert any(isinstance(msg, ModelResponse) for msg in delta)
    assert "ANSWER" in _texts(delta)


async def test_errored_turn_closes_its_dangling_tool_call() -> None:
    """A turn whose tool raises persists a failed return closing the orphan call.

    The raised tool aborts the run, leaving a ``ToolCallPart`` with no return in
    ``capture_run_messages``.  Persisting that verbatim would make the reload
    projection read it as an approval request and the history invalid to replay,
    so ``run_and_persist`` closes it with a failed return carrying the error
    text.  The round-trip then reloads the tool card as an error, not an
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
    assert "simulated tool failure" in str(ret.content)
    assert _tool_states(messages) == ["output-error"]


async def test_approval_pending_turn_keeps_its_dangling_call() -> None:
    """A clean finish awaiting approval leaves its call open, not error-closed.

    An approval-required tool ends the run cleanly with ``DeferredToolRequests``,
    leaving a ``ToolCallPart`` with no return — the same shape as an error
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


def _answer_approval(pending: Sequence[ModelMessage], *, approved: bool) -> UIMessage:
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
                reason=None if approved else _TOOL_DENIED_REASON,
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
    assert str(returns[0].content) == ("written" if approved else _TOOL_DENIED_REASON)


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
    declined = decline_pending_approvals(pending[0])
    assert declined is not None

    returns = _parts([declined], ToolReturnPart)
    assert [r.outcome for r in returns] == ["denied"]
    assert returns[0].tool_call_id == _parts(pending[0], ToolCallPart)[0].tool_call_id
    # The card reloads as a refusal rather than asking for approval all over again.
    assert _tool_states([*pending[0], declined]) == ["output-denied"]


async def test_a_settled_turn_has_no_approval_to_decline() -> None:
    """Only a turn left awaiting approval is declined, never a finished one."""
    settled, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])]
    )

    assert decline_pending_approvals(settled[0]) is None
    assert decline_pending_approvals([]) is None


async def test_generic_run_error_is_recorded_for_reload() -> None:
    """A run that fails before responding records the error on the user turn.

    The stream error is transient, so without this the banner is lost on reload.
    With no assistant response to attach to, the error rides on the last
    projecting message — the user prompt — under ``CHAT_ERROR_KEY``.
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
    assert "provider exploded" in target.metadata[CHAT_ERROR_KEY]
    assert "provider exploded" in body  # the same error streamed live


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

    assert response.metadata == {REASONING_DURATIONS_KEY: [2400]}
    assert "reasoningDurationsMs" in body

    ui = dump_messages_with_ids([("n1", response)])

    assert isinstance(ui[0].metadata, dict)
    assert ui[0].metadata[REASONING_DURATIONS_KEY] == [2400]
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


async def test_import_conversation_rejects_empty_export() -> None:
    """Importing a message-less export fails before any database write.

    The guard runs ahead of persistence, so it is exercised here without a
    live database; the happy path is DB-backed and covered by smoke tests.
    """
    with pytest.raises(ValueError, match="no messages"):
        await import_conversation("user-1", [])


class TestInstructionSnapshots:
    """The system prompts an export reads back out of the stored history."""

    def _pairs(self, *instructions: str | None) -> list[tuple[str, ModelMessage]]:
        pairs: list[tuple[str, ModelMessage]] = []
        for i, text in enumerate(instructions):
            pairs.append(
                (
                    f"n{i}",
                    ModelRequest(
                        parts=[UserPromptPart(content=f"q{i}")], instructions=text
                    ),
                )
            )
        return pairs

    def test_consecutive_identical_prompts_collapse(self) -> None:
        """A prompt that never changed is one snapshot, not one per turn."""
        snapshots = _instruction_snapshots(self._pairs("SYS", "SYS", "SYS"))

        assert len(snapshots) == 1
        assert snapshots[0].message_ids == ["n0", "n1", "n2"]

    def test_a_changed_prompt_starts_a_new_snapshot(self) -> None:
        """Narrowing the document scope mid-conversation stays visible."""
        snapshots = _instruction_snapshots(self._pairs("SYS", "SYS scoped", "SYS"))

        assert [s.text for s in snapshots] == ["SYS", "SYS scoped", "SYS"]
        assert [s.message_ids for s in snapshots] == [["n0"], ["n1"], ["n2"]]

    def test_messages_carrying_no_prompt_are_skipped(self) -> None:
        """Responses hold none, and an imported turn may have lost its own."""
        pairs: list[tuple[str, ModelMessage]] = [
            ("n0", ModelResponse(parts=[TextPart(content="a")])),
            *self._pairs(None),
        ]
        assert _instruction_snapshots(pairs) == []


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
            archive = ConversationArchive(backend=backend, frontend=self._client("live"))
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


def _path() -> list[ActiveNode]:
    """A two-turn active path: user, answer, user, answer."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="q1")]),
        ModelResponse(parts=[TextPart(content="a1")]),
        ModelRequest(parts=[UserPromptPart(content="q2")]),
        ModelResponse(parts=[TextPart(content="a2")]),
    ]
    return [
        ActiveNode(f"n{i}", f"n{i - 1}" if i else None, msg)
        for i, msg in enumerate(messages)
    ]


def test_edit_forks_under_the_edited_message() -> None:
    """An edited user turn is replayed up to (not including) itself."""
    prefix, fork_id = _fork_for_path(_path(), regenerate=False, message_id="n2")

    assert _texts(prefix) == ["q1", "a1"]
    assert fork_id == "n1"


@pytest.mark.parametrize("message_id", ["local-42", "n3"])
def test_submit_not_addressing_a_user_turn_continues_the_conversation(
    message_id: str,
) -> None:
    """Only a user turn is editable; anything else continues at the leaf.

    A turn that failed before its first write leaves the client holding an id
    no node carries, and the AI SDK auto-continues an approved tool call with
    the *assistant* message's id — forking there would drop the very response
    holding the approved call.
    """
    prefix, fork_id = _fork_for_path(_path(), regenerate=False, message_id=message_id)

    assert _texts(prefix) == ["q1", "a1", "q2", "a2"]
    assert fork_id == "n3"


def test_regenerate_forks_at_the_nearest_user_turn() -> None:
    """Regenerating an answer replays through the user turn that asked for it."""
    prefix, fork_id = _fork_for_path(_path(), regenerate=True, message_id="n3")

    assert _texts(prefix) == ["q1", "a1", "q2"]
    assert fork_id == "n2"


def test_dump_messages_with_ids_anchors_node_ids() -> None:
    """Each ``UIMessage.id`` is its head node id; tool-return rows emit none."""
    pairs: list[tuple[str, ModelMessage]] = [
        ("n1", ModelRequest(parts=[UserPromptPart(content="q")])),
        (
            "n2",
            ModelResponse(
                parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")]
            ),
        ),
        (
            "n3",
            ModelRequest(
                parts=[ToolReturnPart(tool_name="t", content="r", tool_call_id="c1")]
            ),
        ),
        ("n4", ModelResponse(parts=[TextPart(content="answer")])),
    ]

    ui = dump_messages_with_ids(pairs, siblings={"n4": ["n4", "n5"]})

    ids = [msg.id for msg in ui]
    # The user request and each assistant response head are addressable; the
    # tool-return-only request emits no UIMessage.
    assert ids == ["n1", "n2", "n4"]

    # branchCount / branchIndex are derived from the sibling list.
    answer = next(msg for msg in ui if msg.id == "n4")
    assert isinstance(answer.metadata, dict)
    assert answer.metadata["branch"]["branchCount"] == 2
    assert answer.metadata["branch"]["branchIndex"] == 0
    assert answer.metadata["branch"]["siblingIds"] == ["n4", "n5"]
