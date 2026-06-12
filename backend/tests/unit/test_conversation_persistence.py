"""Tests for mirroring a chat turn into storage.

The conversation repository stores the run's whole message list each turn
rather than appending a delta, so the stored copy stays equal to the live
state through errors and retries.  These exercise the parts that need no
database: ``run_and_persist`` (which picks what to persist on clean versus
interrupted finishes) and ``_restore_tool_metadata`` (which heals the
tool-output metadata the browser's echoed history drops).  The actual SQL
write in ``replace_messages`` is covered by manual smoke tests.
"""

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, ModelMessagesTypeAdapter, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from starlette.responses import StreamingResponse

from hivegent.db.conversations import _restore_tool_metadata
from hivegent.server.vercel import ChatAdapter, run_and_persist


def _texts(messages: Sequence[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, (UserPromptPart, TextPart)) and isinstance(part.content, str)
    ]


async def _persisted_turn(
    ui_messages: list[UIMessage], *, tool_raises: bool = False
) -> list[ModelMessage]:
    """Run one turn through ``run_and_persist`` and return what it stored."""
    agent = Agent(model=TestModel(custom_output_text="ANSWER"), output_type=str)

    @agent.tool
    def lookup(ctx: RunContext[None], query: str) -> str:
        if tool_raises:
            raise RuntimeError("simulated tool failure mid-run")
        return "DOC-CONTENT"

    adapter = ChatAdapter[None, str](
        agent=agent,
        run_input=SubmitMessage(
            id="c1", messages=ui_messages, trigger="submit-message"
        ),
    )
    recorded: list[list[ModelMessage]] = []

    async def persist(messages: Sequence[ModelMessage]) -> None:
        recorded.append(list(messages))

    response = await run_and_persist(adapter, adapter.run_stream(), persist=persist)
    assert isinstance(response, StreamingResponse)
    async for _ in response.body_iterator:
        pass

    assert len(recorded) == 1
    return recorded[0]


async def test_clean_turn_persists_whole_conversation() -> None:
    """A successful second turn mirrors the full history, not just its delta.

    The first turn is dumped to UI messages and echoed back with a new
    prompt appended — the exact round-trip the frontend performs — and the
    persisted set keeps the echoed turn so a reload sees it.
    """
    first = await _persisted_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])]
    )
    echoed = [*ChatAdapter.dump_messages(first)]
    echoed.append(UIMessage(id="m2", role="user", parts=[TextUIPart(text="q2")]))

    persisted = await _persisted_turn(echoed)

    texts = _texts(persisted)
    assert "q1" in texts
    assert "q2" in texts
    assert "ANSWER" in texts


async def test_errored_turn_keeps_the_partial_transcript() -> None:
    """A turn whose tool fails keeps the prompt and the partial it produced.

    ``capture_run_messages`` exposes the live run, so the user's prompt and
    the tool call streamed before the failure are stored — only the answer
    that never came is missing.
    """
    persisted = await _persisted_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])],
        tool_raises=True,
    )

    assert "q1" in _texts(persisted)
    assert any(
        isinstance(part, ToolCallPart)
        for message in persisted
        for part in message.parts
    )
    assert "ANSWER" not in _texts(persisted)


def _content(messages: Sequence[ModelMessage]) -> Any:
    """JSON dump with volatile request-part timestamps stripped.

    ``UserPromptPart`` / ``ToolReturnPart`` carry a ``timestamp`` that the
    Vercel round-trip regenerates on load; it is bookkeeping, not content, so
    drop it before comparing what a re-persist would actually store.
    """
    dumped = ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "timestamp"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(dumped)


def test_round_trip_is_lossless_except_restored_metadata() -> None:
    """dump -> browser echo -> load -> restore loses no message content.

    Full mirror re-persists the echoed history every turn, so the round-trip
    must not silently drop anything.  pydantic-ai preserves all content except
    ``ToolReturnPart.metadata`` (which ``_restore_tool_metadata`` re-supplies)
    and the regenerated request-part timestamps; this pins that contract so an
    upgrade that starts losing more fails loudly here.
    """
    stored: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="explain the architecture")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="I should search the docs first"),
                TextPart(content="Let me look that up."),
                ToolCallPart(
                    tool_name="search", args={"query": "architecture"}, tool_call_id="c1"
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="found 3 relevant chunks",
                    tool_call_id="c1",
                    metadata=DataChunk(
                        type="data-tool-output", id="c1", data={"chunks": [1, 2, 3]}
                    ),
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="The system has two layers.")]),
    ]

    reloaded = ChatAdapter.load_messages(ChatAdapter.dump_messages(stored))

    # The lone casualty of the round-trip is the tool-output metadata.
    tool_return = reloaded[2].parts[0]
    assert isinstance(tool_return, ToolReturnPart) and tool_return.metadata is None

    # Restoring it from the prior rows makes the mirror byte-for-byte faithful.
    _restore_tool_metadata(reloaded, stored)
    assert _content(reloaded) == _content(stored)


def test_restore_tool_metadata_heals_echoed_history() -> None:
    """Prior rows re-supply tool-output metadata the browser echo dropped."""
    kept = DataChunk(type="data-tool-output", id="c1", data={"kept": True})
    live = DataChunk(type="data-tool-output", id="c2", data={"live": True})
    existing = [
        ModelRequest(parts=[UserPromptPart(content="q")]),
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="t", content="r", tool_call_id="c1", metadata=kept
                )
            ]
        ),
    ]
    incoming = [
        ModelRequest(  # echoed history: metadata stripped on the way back
            parts=[ToolReturnPart(tool_name="t", content="r", tool_call_id="c1")]
        ),
        ModelRequest(  # this turn's fresh tail: live metadata, must be left alone
            parts=[
                ToolReturnPart(
                    tool_name="t2", content="r2", tool_call_id="c2", metadata=live
                )
            ]
        ),
    ]

    _restore_tool_metadata(incoming, existing)

    restored = incoming[0].parts[0]
    untouched = incoming[1].parts[0]
    assert isinstance(restored, ToolReturnPart) and restored.metadata == kept
    assert isinstance(untouched, ToolReturnPart) and untouched.metadata == live
