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
from typing import Any

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
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai.output import OutputSpec
from starlette.responses import StreamingResponse

import hivegent.server.vercel as vercel_module
from hivegent.db.conversations import import_conversation
from hivegent.server.vercel import (
    CHAT_ERROR_KEY,
    ChatAdapter,
    PersistTurn,
    REASONING_DURATIONS_KEY,
    dump_messages_with_ids,
    run_and_persist,
)


def _texts(messages: Sequence[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, (UserPromptPart, TextPart))
        and isinstance(part.content, str)
    ]


def _tool_states(messages: Sequence[ModelMessage]) -> list[str | None]:
    """Reload states of the tool cards a message list projects to."""
    ui = dump_messages_with_ids([(f"n{i}", m) for i, m in enumerate(messages)])
    return [
        getattr(part, "state", None)
        for message in ui
        for part in message.parts
        if str(getattr(part, "type", "")).startswith("tool-")
    ]


def _adapter(
    ui_messages: list[UIMessage],
    *,
    model: Model | None = None,
    tool_raises: bool = False,
    tool_needs_approval: bool = False,
) -> ChatAdapter[None, str]:
    output_type: OutputSpec[Any] = str
    if tool_needs_approval:
        output_type = [str, DeferredToolRequests]

    agent = Agent(
        model=model or TestModel(custom_output_text="ANSWER"), output_type=output_type
    )

    if tool_raises:

        @agent.tool_plain
        def boom() -> str:
            raise RuntimeError("simulated tool failure mid-run")

    if tool_needs_approval:

        @agent.tool_plain(requires_approval=True)
        def write(value: str) -> str:
            return "written"

    return ChatAdapter[None, str](
        agent=agent,
        run_input=SubmitMessage(
            id="c1", messages=ui_messages, trigger="submit-message"
        ),
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
    call = next(
        part
        for message in messages
        for part in message.parts
        if isinstance(part, ToolCallPart)
    )
    ret = next(
        part
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == call.tool_call_id
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

    assert not any(
        isinstance(part, ToolReturnPart)
        for message in messages
        for part in message.parts
    )
    assert _tool_states(messages) == ["approval-requested"]


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
    target = recorded[0][-1]

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
