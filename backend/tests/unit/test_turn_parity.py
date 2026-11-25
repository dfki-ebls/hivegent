"""Golden turns pinning that a streamed turn ends as the turn a reload shows.

Each scenario drives turns through the chat route over pydantic-ai's
in-memory conversation store and records what the client of its last turn sees: the messages it held,
the chunks it streamed, and the stored turn it swaps in once the stream ends.
``frontend/src/test/lib/turn-parity.test.ts`` replays the chunks through the
AI SDK and checks that the swap changes nothing on screen.  The fixtures are
regenerated with ``UPDATE_FIXTURES=1``.
"""

import asyncio
import base64
import json
import os
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from exar.memory import InMemoryMemory
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.conversation_store import InMemoryConversationStore
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from starlette.requests import Request
from starlette.responses import StreamingResponse

from hivegent.agents import RunPrefix, UserDeps, build_capabilities
from hivegent.agents.tools import memory
from hivegent.db.conversations import OwnedConversation
from hivegent.runs import runs
from hivegent.server.routes.conversations import compaction, history, turn
from hivegent.server.vercel import TURN_CHUNK_TYPE, UI_MESSAGES
from hivegent.store import User
from hivegent.types import ChatRequest
from tests.helpers import OVERFLOW, label_id, owned, png_bytes, sse_chunks

_FIXTURES = Path(__file__).parents[3] / "frontend/src/test/fixtures/turns"

_USER = User("u")

type Stream = AsyncIterator[str | DeltaToolCalls | dict[int, DeltaThinkingPart]]
type Script = Callable[[Sequence[ModelMessage]], Stream]


@dataclass
class _Tree:
    """The conversation a scenario drives, and whether its record exists yet."""

    conversation: OwnedConversation
    exists: bool = False


def _toolset() -> FunctionToolset[UserDeps]:
    """Tools a script calls: one that answers, one that asks for approval, one that fails."""
    tools = FunctionToolset[UserDeps]()

    @tools.tool_plain(metadata={"effect": "read"})
    async def lookup(term: str) -> str:
        """Look *term* up, the first term last, so calls finish out of order."""
        await asyncio.sleep(0.05 if term == "a" else 0)

        return f"found {term}"

    @tools.tool(metadata={"effect": "read"})
    def write(ctx: RunContext[UserDeps], value: str) -> str:
        """Write *value*."""
        if not ctx.tool_call_approved:
            raise ApprovalRequired({"value": value})

        return "written"

    @tools.tool_plain(metadata={"effect": "read"})
    def boom() -> str:
        """Fail."""
        raise RuntimeError("simulated tool failure")

    return tools


def _summarizing(messages: Sequence[ModelMessage]) -> bool:
    last = messages[-1].parts[-1]

    return isinstance(last, UserPromptPart) and "handover note" in str(last.content)


def _model(script: Script) -> FunctionModel:
    """A model streaming *script*, which also answers the summary a compaction asks for."""

    async def stream(messages: list[ModelMessage], _info: AgentInfo) -> Stream:
        if _summarizing(messages):
            yield "the gist"
            return

        async for delta in script(messages):
            yield delta

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        assert _summarizing(messages)

        return ModelResponse(parts=[TextPart("the gist")], model_name="m")

    return FunctionModel(respond, stream_function=stream)


def _call(name: str, args: dict[str, Any], id: str) -> DeltaToolCalls:
    return {0: DeltaToolCall(name, json.dumps(args), tool_call_id=id)}


def _answered(messages: Sequence[ModelMessage]) -> bool:
    """Whether the run already got back a result of a call it made itself."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name != "read_memory"
        for part in messages[-1].parts
    ) and isinstance(messages[-1], ModelRequest)


async def _text(_messages: Sequence[ModelMessage]) -> Stream:
    yield {0: DeltaThinkingPart(content="weighing options")}
    yield "Hello there."


async def _tools(messages: Sequence[ModelMessage]) -> Stream:
    if _answered(messages):
        yield "Both found."
        return

    yield {
        0: DeltaToolCall("lookup", json.dumps({"term": "a"}), tool_call_id="c1"),
        1: DeltaToolCall("lookup", json.dumps({"term": "b"}), tool_call_id="c2"),
    }


async def _approval(messages: Sequence[ModelMessage]) -> Stream:
    if _answered(messages):
        yield "Written."
        return

    yield "Writing it."
    yield _call("write", {"value": "x"}, "w1")


async def _question(_messages: Sequence[ModelMessage]) -> Stream:
    question = {
        "header": "Scope",
        "question": "Which?",
        "options": [{"label": "All"}, {"label": "Some"}],
    }
    yield _call("ask_user", {"questions": [question]}, "q1")


async def _failing(_messages: Sequence[ModelMessage]) -> Stream:
    yield "Trying."
    yield _call("boom", {}, "b1")


def _overflowing(script: Script) -> Script:
    """*script*, but the context window refuses its first prompt after a compaction-free history."""

    async def stream(messages: Sequence[ModelMessage]) -> Stream:
        compacted = any(
            isinstance(m, ModelRequest)
            and any(type(p).__name__ == "CompactionPart" for p in m.parts)
            for m in messages
        )

        if len(messages) > 2 and not compacted:
            raise OVERFLOW

        async for delta in script(messages):
            yield delta

    return stream


@pytest.fixture()
def tree(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    memory_store: InMemoryConversationStore,
) -> _Tree:
    tree = _Tree(owned("parity"))

    def no_memory(user: User) -> InMemoryMemory[str, str]:
        return InMemoryMemory()

    monkeypatch.setattr(memory, "user_memory", no_memory)

    return tree


def _prefix(run_prefix: RunPrefix, script: Script) -> RunPrefix:
    return replace(
        run_prefix,
        model=_model(script),
        capabilities=build_capabilities(run_prefix.deps, extra=[_toolset()]),
    )


async def _messages(tree: _Tree) -> list[dict[str, Any]]:
    """The conversation as a client loads it."""
    return UI_MESSAGES.dump_python(
        await history.active_messages(tree.conversation), mode="json", by_alias=True
    )


async def _turn(
    tree: _Tree,
    monkeypatch: pytest.MonkeyPatch,
    prefix: RunPrefix,
    sent: dict[str, Any] | None,
    *,
    stop_after: int | None = None,
    **body: Any,
) -> list[dict[str, Any]]:
    """Run one turn sending *sent*, returning the chunks its client receives."""

    async def build_run_prefix(*_args: object) -> RunPrefix:
        return prefix

    monkeypatch.setattr(turn, "build_run_prefix", build_run_prefix)
    request = ChatRequest.model_validate(
        {"id": str(tree.conversation.id), "messages": [sent] if sent else [], **body}
    )
    response = await turn.create_conversation_chat(
        (tree.conversation, tree.exists),
        _USER,
        request,
        Request({"type": "http", "headers": []}),
        None,
    )
    assert isinstance(response, StreamingResponse)
    chunks: list[dict[str, Any]] = []

    async for encoded in response.body_iterator:
        chunks.extend(
            sse_chunks(encoded if isinstance(encoded, str) else bytes(encoded).decode())
        )

        if stop_after is not None and len(chunks) == stop_after:
            await runs.cancel(tree.conversation)

    tree.exists = True
    await asyncio.sleep(0)

    return chunks


def _prompt(text: str, id: str) -> dict[str, Any]:
    return {
        "id": str(label_id(id)),
        "role": "user",
        "parts": [{"type": "text", "text": text}],
    }


_VOLATILE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|pyd_ai_[0-9a-f]+)$"
)
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})?$")


def _stable(
    value: object, ids: dict[str, str] | None = None, *, durations: bool = False
) -> object:
    """*value* with its generated ids, times, and *durations* replaced by stable placeholders."""
    ids = {} if ids is None else ids

    match value:
        case dict():
            return {
                ids.setdefault(k, f"id-{len(ids)}")
                if _VOLATILE.match(k)
                else k: _stable(
                    v, ids, durations=durations or k == "reasoningDurationsMs"
                )
                for k, v in value.items()
            }

        case list():
            return [_stable(v, ids, durations=durations) for v in value]

        case int() if durations:
            return 0

        case str() if _VOLATILE.match(value):
            return ids.setdefault(value, f"id-{len(ids)}")

        case str() if _TIME.match(value):
            return "<time>"

        case _:
            return value


def _record(
    name: str, initial: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> None:
    """Pin the turn the client of *initial* streamed as *chunks*."""
    snapshots = [c for c in chunks if c.get("type") == TURN_CHUNK_TYPE]

    # The client stops reading at an error, so the stored turn comes first.
    assert len(snapshots) == 1
    assert all(c["type"] != "error" for c in chunks[: chunks.index(snapshots[0])])

    fixture = _stable({"initial": initial, "chunks": chunks})
    path = _FIXTURES / f"{name}.json"
    text = json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"

    if os.environ.get("UPDATE_FIXTURES"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    assert path.read_text() == text, f"{path.name} is stale, set UPDATE_FIXTURES=1"


async def _submit(
    tree: _Tree,
    monkeypatch: pytest.MonkeyPatch,
    prefix: RunPrefix,
    name: str,
    text: str,
    **options: Any,
) -> list[dict[str, Any]]:
    sent = _prompt(text, name)
    initial = [*await _messages(tree), sent]
    chunks = await _turn(tree, monkeypatch, prefix, sent, **options)
    _record(name, initial, chunks)

    return chunks


@pytest.mark.parametrize(
    ("name", "script"),
    [("text", _text), ("tools", _tools), ("question", _question), ("error", _failing)],
)
async def test_a_turn_ends_as_it_reloads(
    tree: _Tree,
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    name: str,
    script: Script,
) -> None:
    await _submit(tree, monkeypatch, _prefix(run_prefix, script), name, "Go.")


async def test_an_attachment_keeps_its_filename(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    """The stored turn names an attached image as the client sent it."""
    prefix = _prefix(
        replace(run_prefix, llm=replace(run_prefix.llm, vision=True)), _text
    )
    sent = _prompt("Look.", "attachment")
    sent["parts"].append(
        {
            "type": "file",
            "mediaType": "image/png",
            "filename": "diagram.png",
            "url": f"data:image/png;base64,{base64.b64encode(png_bytes()).decode()}",
        }
    )
    initial = [*await _messages(tree), sent]
    _record("attachment", initial, await _turn(tree, monkeypatch, prefix, sent))


@pytest.mark.parametrize("race_at_claim", [False, True], ids=["after-open", "at-claim"])
async def test_a_racing_turn_appends_to_the_history_it_replays(
    tree: _Tree,
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    race_at_claim: bool,
) -> None:
    """A stale existence check or a lost claim cannot replace the active transcript."""
    prefix = _prefix(run_prefix, _text)
    await _turn(tree, monkeypatch, prefix, _prompt("Hi.", "first"))
    before = await _messages(tree)
    tree.exists = False

    async def absent(_conversation: OwnedConversation) -> None:
        return None

    with monkeypatch.context() as racing:
        if race_at_claim:
            racing.setattr(turn, "load_conversation", absent)
            racing.setattr(history, "load_conversation", absent)

        await _turn(tree, monkeypatch, prefix, _prompt("Again.", "second"))

    assert (await _messages(tree))[:-2] == before


async def test_a_continued_turn_ends_as_it_reloads(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    prefix = _prefix(run_prefix, _approval)
    await _submit(tree, monkeypatch, prefix, "approval", "Write x.")

    *_, paused = await _messages(tree)
    paused["parts"] = [
        {
            **part,
            "state": "approval-responded",
            "approval": {**part["approval"], "approved": True},
        }
        if part.get("state") == "approval-requested"
        else part
        for part in paused["parts"]
    ]
    initial = [*(await _messages(tree))[:-1], paused]
    chunks = await _turn(tree, monkeypatch, prefix, paused, messageId=paused["id"])
    _record("approval-continued", initial, chunks)


async def test_an_overtaken_approval_reloads_as_it_was_left(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    await _submit(
        tree, monkeypatch, _prefix(run_prefix, _approval), "abandon", "Write x."
    )
    await _submit(
        tree, monkeypatch, _prefix(run_prefix, _text), "abandoned", "Never mind."
    )


async def test_a_compacted_turn_ends_as_it_reloads(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    await _submit(tree, monkeypatch, _prefix(run_prefix, _text), "compact-first", "Hi.")
    await _submit(
        tree,
        monkeypatch,
        _prefix(run_prefix, _overflowing(_text)),
        "compacted",
        "Again.",
    )


async def test_a_stopped_turn_ends_as_it_reloads(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    async def hanging(_messages: Sequence[ModelMessage]) -> Stream:
        yield "Partial"
        await asyncio.Event().wait()
        yield "never"

    await _submit(
        tree, monkeypatch, _prefix(run_prefix, hanging), "stopped", "Go.", stop_after=4
    )


async def test_a_turn_that_was_not_stored_keeps_what_it_streamed(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    """Only the turn's own write settles it, so a failed one leaves the client its turn to retry."""

    async def failing(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr(compaction.store, "save", failing)
    chunks = await _turn(
        tree, monkeypatch, _prefix(run_prefix, _text), _prompt("Go.", "unsaved")
    )

    assert all(chunk["type"] != TURN_CHUNK_TYPE for chunk in chunks)
    assert chunks[-1]["type"] == "error"


async def test_a_regenerated_turn_ends_as_it_reloads(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    prefix = _prefix(run_prefix, _text)
    await _submit(tree, monkeypatch, prefix, "regenerate-first", "Hi.")

    *asked, answer = await _messages(tree)
    chunks = await _turn(
        tree,
        monkeypatch,
        prefix,
        None,
        trigger="regenerate-message",
        messageId=answer["id"],
    )
    _record("regenerated", asked, chunks)


async def test_an_edited_turn_ends_as_it_reloads(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    prefix = _prefix(run_prefix, _text)
    await _submit(tree, monkeypatch, prefix, "edit-first", "Hi.")

    *before, asked, _ = await _messages(tree)
    edited = _prompt("Hello instead.", "edited")
    chunks = await _turn(tree, monkeypatch, prefix, edited, messageId=asked["id"])
    _record("edited", [*before, edited], chunks)


async def test_a_turn_after_the_compact_button_ends_as_it_reloads(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    """The summary stays in the reply it compacted, the next turn after it."""
    prefix = _prefix(run_prefix, _text)
    await _submit(tree, monkeypatch, prefix, "compact-button-first", "Hi.")

    initial = await _messages(tree)
    chunks = await _turn(tree, monkeypatch, prefix, None, compact=True)
    _record("compact-button", initial, chunks)
    await _submit(tree, monkeypatch, prefix, "after-compact-button", "Again.")


async def test_a_regenerate_keeps_the_compaction_made_for_its_prompt(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    """The summary covers only the history before the prompt, so a regenerate reuses it rather than overflowing again."""
    refused: list[None] = []

    async def counted(messages: Sequence[ModelMessage]) -> Stream:
        if len(messages) > 2 and not any(
            type(p).__name__ == "CompactionPart" for m in messages for p in m.parts
        ):
            refused.append(None)

        async for delta in _overflowing(_text)(messages):
            yield delta

    prefix = _prefix(run_prefix, counted)
    await _turn(tree, monkeypatch, _prefix(run_prefix, _text), _prompt("Hi.", "keep-1"))
    await _turn(tree, monkeypatch, prefix, _prompt("Again.", "keep-2"))
    assert len(refused) == 1

    *_, answer = await _messages(tree)
    await _turn(
        tree,
        monkeypatch,
        prefix,
        None,
        trigger="regenerate-message",
        messageId=answer["id"],
    )

    assert len(refused) == 1


async def test_a_turn_runs_as_its_conversation(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    """Its messages and spans name the conversation, not an id pydantic-ai made up."""
    seen: list[str | None] = []

    async def script(messages: Sequence[ModelMessage]) -> Stream:
        seen.extend(m.conversation_id for m in messages)
        yield "Hi."

    deps = replace(run_prefix.deps, conversation=tree.conversation)
    prefix = replace(_prefix(run_prefix, script), deps=deps)
    await _turn(tree, monkeypatch, prefix, _prompt("Go.", "go"))

    assert set(seen) == {str(tree.conversation.id)}
