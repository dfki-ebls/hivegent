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
from datetime import UTC, datetime

from pydantic_ai import Agent
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
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from starlette.responses import StreamingResponse

from hivegent.db.conversations import ExportMessage, _remapped_nodes
from hivegent.db.models import MessageKind
from hivegent.server.vercel import (
    ChatAdapter,
    PersistTurn,
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


def _adapter(
    ui_messages: list[UIMessage],
    *,
    model: Model | None = None,
    tool_raises: bool = False,
) -> ChatAdapter[None, str]:
    agent = Agent(
        model=model or TestModel(custom_output_text="ANSWER"), output_type=str
    )

    if tool_raises:

        @agent.tool_plain
        def boom() -> str:
            raise RuntimeError("simulated tool failure mid-run")

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
    persist: PersistTurn | None = None,
) -> tuple[list[list[ModelMessage]], str]:
    """Run one turn through ``run_and_persist``; return recorded turns + body."""
    adapter = _adapter(ui_messages, model=model, tool_raises=tool_raises)
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


async def test_errored_turn_keeps_the_prompt() -> None:
    """A turn whose tool fails keeps its prompt and the completed tool call.

    ``capture_run_messages`` holds whatever completed before the failure, which
    the Vercel adapter turns into an in-band error chunk, so the persist still
    fires on the clean-drain path.
    """
    recorded, _ = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_raises=True,
    )

    assert "q1" in _texts(recorded[0])
    assert any(
        isinstance(part, ToolCallPart)
        for message in recorded[0]
        for part in message.parts
    )
    assert "ANSWER" not in _texts(recorded[0])


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


async def test_persist_failure_hard_fails_with_an_error_chunk() -> None:
    """A failed write surfaces a trailing error chunk on a clean drain."""

    async def failing(messages: Sequence[ModelMessage]) -> None:
        raise RuntimeError("db down")

    _, body = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        persist=failing,
    )

    assert "Failed to save the conversation" in body


def test_remapped_nodes_rewires_ids_and_tolerates_missing_parents() -> None:
    """Import re-keys nodes, remaps parent links, and orphans dangling refs.

    A child whose parent is absent from the dump (e.g. exported from a tree the
    importer never had) becomes a root rather than referencing a missing id, so
    a foreign cross-collection backup still imports cleanly.
    """
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def _msg(node_id: str, parent_id: str | None) -> ExportMessage:
        return ExportMessage(
            id=node_id,
            parent_id=parent_id,
            kind=MessageKind.REQUEST,
            created_at=now,
            payload={"kind": "request", "parts": []},
        )

    nodes = _remapped_nodes([_msg("a", None), _msg("b", "a"), _msg("c", "missing")])

    new_ids = [node.id for node in nodes]
    assert len(set(new_ids)) == 3  # all fresh and unique
    assert all(node_id not in {"a", "b", "c"} for node_id in new_ids)

    assert nodes[0].parent_id is None  # root stays a root
    assert nodes[1].parent_id == nodes[0].id  # child rewired to its parent's new id
    assert nodes[2].parent_id is None  # dangling parent dropped to a root
    assert all(node.created_at == now for node in nodes)


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
