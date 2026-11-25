"""Tests for the subagents of a chat turn, delegated to with pydantic-ai-harness."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from exar.tools import effect
from pydantic_ai.conversation_store import InMemoryConversationStore
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.ui.vercel_ai.request_types import DataUIPart
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness.sub_agents import SubAgents
from starlette.requests import Request
from starlette.responses import StreamingResponse

from hivegent.agents import RunPrefix
from hivegent.agents import subagents as subagents_module
from hivegent.agents.common import AgentTools
from hivegent.agents.debugger import conversation_steps
from hivegent.agents.subagents import Subagent, delegation
from hivegent.agents.transcripts import (
    SUBAGENT_CHUNK_TYPE,
    SubagentStep,
    SubagentTranscript,
    SubagentUpdate,
    subagent_transcript,
)
from hivegent.db.conversations import is_user_request, load_conversation
from hivegent.server.routes.conversations import history, turn
from hivegent.store import User, UserScope
from hivegent.types import ChatRequest, DocumentFilter, Mode
from tests.helpers import label_id, owned, sse_chunks

type Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]
"""Answers an agent."""

_CONVERSATION = owned("subagents")


def _first_prompt(messages: list[ModelMessage]) -> str:
    return next(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )


def _responses(messages: list[ModelMessage]) -> int:
    """The responses of the model, without the memory read the turn opens with."""
    return sum(
        isinstance(message, ModelResponse) and message.model_name is not None
        for message in messages
    )


def _call(tool: str, call_id: str, **args: object) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args, tool_call_id=call_id)])


def _text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def _echo(messages: list[ModelMessage]) -> ModelResponse:
    """The text of the last result or prompt an agent got."""
    last = messages[-1].parts[-1]
    assert isinstance(last, ToolReturnPart | UserPromptPart)

    return _text(str(last.content))


def _model(scripts: dict[str, Script]) -> FunctionModel:
    """Answers each agent by the script of its first prompt, streamed as a turn needs."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        for index, part in enumerate(
            scripts[_first_prompt(messages)](messages, info).parts
        ):
            if isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        part.tool_name,
                        json.dumps(part.args),
                        tool_call_id=part.tool_call_id,
                    )
                }
            elif isinstance(part, TextPart):
                yield part.content

    return FunctionModel(stream_function=stream)


@pytest.fixture(autouse=True)
def _store(data_dir: Path, memory_store: InMemoryConversationStore) -> None:
    """Keep the turns in memory."""


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    scripts: dict[str, Script],
    *,
    mode: Mode = "interactive",
) -> list[dict[str, Any]]:
    """Run the turn asking ``question`` through the chat route, returning the chunks its client receives."""
    deps = replace(run_prefix.deps, mode=mode)
    prefix = replace(run_prefix, deps=deps, model=_model(scripts))
    prefix = replace(prefix, capabilities=[*prefix.capabilities, *delegation(prefix)])

    async def build_run_prefix(*_args: object) -> RunPrefix:
        return prefix

    monkeypatch.setattr(turn, "build_run_prefix", build_run_prefix)
    prompt = {
        "id": str(label_id("question")),
        "role": "user",
        "parts": [{"type": "text", "text": "question"}],
    }
    request = ChatRequest.model_validate(
        {"id": str(_CONVERSATION.id), "messages": [prompt]}
    )
    response = await turn.create_conversation_chat(
        (_CONVERSATION, False),
        User("u"),
        request,
        Request({"type": "http", "headers": []}),
    )
    assert isinstance(response, StreamingResponse)
    chunks = [
        chunk
        async for encoded in response.body_iterator
        for chunk in sse_chunks(
            encoded if isinstance(encoded, str) else bytes(encoded).decode()
        )
    ]
    await asyncio.sleep(0)

    return chunks


async def _reloaded(call: str) -> SubagentTranscript:
    """The transcript a reload shows on the call *call*."""
    [data] = [
        part.data
        for message in await history.active_messages(_CONVERSATION)
        for part in message.parts
        if isinstance(part, DataUIPart) and part.id == call
    ]

    return SubagentTranscript.model_validate(data)


async def _stored() -> list[ModelMessage]:
    loaded = await load_conversation(_CONVERSATION)
    assert loaded is not None

    return list(loaded.messages)


async def test_a_delegation_renders_under_its_call_live_and_on_reload(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # The read mode offers delegation, which changes nothing itself.
        assert "delegate_task" in {tool.name for tool in info.function_tools}

        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="explore", task="look")

        return _echo(messages)

    def explore(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("list_documents", "l1")

        return _text("found it")

    chunks = await _turn(
        monkeypatch, run_prefix, {"question": chat, "look": explore}, mode="read"
    )
    live = [chunk["data"] for chunk in chunks if chunk["type"] == SUBAGENT_CHUNK_TYPE]
    steps = (
        SubagentStep(kind="tool", tool_name="list_documents"),
        SubagentStep(kind="message"),
    )
    reloaded = await _reloaded("d1")
    loaded = await load_conversation(_CONVERSATION)
    assert loaded is not None
    *_, answer = loaded.messages

    # Each step streams under the call that delegated it, and the stored history renders the same.
    assert SubagentUpdate.model_validate(live[-1]) == SubagentUpdate(
        tool_call_id="d1",
        transcript=SubagentTranscript(transcript="subagent", steps=steps),
    )
    assert reloaded == SubagentTranscript(transcript="subagent", steps=steps)
    assert isinstance(answer, ModelResponse) and "found it" in (answer.text or "")
    # The debugger lists the subagent's steps after the call, and the turn's usage counts it.
    assert ("agent", "d1", "tool list_documents, message") in [
        (step.kind, step.call_id, step.text)
        for step in conversation_steps(loaded.messages)
    ]
    assert loaded.usage.requests == 4


@pytest.mark.parametrize(
    ("timeout", "steps", "answer"),
    [
        (1.0, ("wait", "wait", None), "partial findings"),
        (0.2, ("wait",), "exceeded"),
    ],
)
async def test_a_subagent_out_of_time_reports_and_keeps_its_steps(
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    timeout: float,
    steps: tuple[str | None, ...],
    answer: str,
) -> None:
    """Past the soft deadline its calls are refused and it reports, past the timeout it is cut off, its steps kept either way.

    The deadline changes neither its tools nor its instructions, so every request it sends has the same prefix.
    """

    @effect("read")
    async def wait() -> str:
        """Waits."""
        await asyncio.sleep(0.85)
        return "waited"

    monkeypatch.setattr(subagents_module.settings.subagents, "timeout_seconds", timeout)
    monkeypatch.setitem(
        subagents_module.SUBAGENTS,  # pyright: ignore[reportArgumentType]
        "sleeper",
        Subagent(description="Waits.", tools=(AgentTools(factory=lambda _: [wait]),)),
    )

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="sleeper", task="nap")

        return _echo(messages)

    prefixes: list[tuple[str | None, list[ToolDefinition]]] = []

    def sleeper(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prefixes.append((info.instructions, info.function_tools))

        if "Report your findings now" in str(messages[-1]):
            return _text("partial findings")

        return _call("wait", f"w{len(prefixes)}")

    await _turn(monkeypatch, run_prefix, {"question": chat, "nap": sleeper})
    *_, reply = await _stored()

    assert isinstance(reply, ModelResponse) and answer in (reply.text or "")
    assert all(prefix == prefixes[0] for prefix in prefixes)
    assert await _reloaded("d1") == SubagentTranscript(
        transcript="subagent",
        steps=tuple(
            SubagentStep(kind="message")
            if name is None
            else SubagentStep(kind="tool", tool_name=name)
            for name in steps
        ),
    )


async def test_a_background_report_is_no_prompt_of_the_user(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        match _responses(messages):
            case 0:
                return _call(
                    "delegate_task",
                    "d1",
                    agent_name="explore",
                    task="look",
                    background=True,
                )
            case 1:
                return _text("waiting")
            case _:
                return _echo(messages)

    monkeypatch.setattr(subagents_module.settings.llm, "tool_output_max_chars", 100)
    await _turn(
        monkeypatch,
        run_prefix,
        {
            "question": chat,
            "look": lambda _messages, _info: _text("found it" + "x" * 500),
        },
    )
    stored = await _stored()
    shown = await history.active_messages(_CONVERSATION)

    # The report reaches the main agent, bounded like a tool return, but neither as a user turn nor on screen.
    assert "found it" in str(stored[-2].parts)
    assert "Cut short of its 508 characters" in str(stored[-2].parts)
    assert "x" * 100 not in str(stored[-2].parts)
    assert [is_user_request(message) for message in stored].count(True) == 1
    assert [message.role for message in shown] == ["user", "assistant"]


async def test_a_subagent_refuses_what_would_ask(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    sent: list[str] = []

    @effect("execute")
    async def send(text: str) -> str:
        """Sends a text."""
        sent.append(text)
        return f"sent {text}"

    monkeypatch.setitem(
        subagents_module.SUBAGENTS,  # pyright: ignore[reportArgumentType]
        "mailer",
        Subagent(description="Sends.", tools=(AgentTools(factory=lambda _: [send]),)),
    )

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="mailer", task="mail")

        return _echo(messages)

    def mailer(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("send", "s1", text="hi")

        return _echo(messages)

    chunks = await _turn(
        monkeypatch, run_prefix, {"question": chat, "mail": mailer}, mode="write"
    )

    # The write mode asks before any execution, which a subagent refuses rather than asking.
    assert sent == []
    assert any("cannot ask the user" in str(chunk.get("delta", "")) for chunk in chunks)
    assert all(chunk["type"] != "tool-approval-request" for chunk in chunks)


async def test_the_turn_budget_bounds_its_subagents(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return _call("delegate_task", "d1", agent_name="explore", task="look")

    def explore(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return _call("list_documents", f"l{_responses(messages)}")

    monkeypatch.setattr(turn, "turn_usage_limits", UsageLimits(request_limit=3))
    chunks = await _turn(monkeypatch, run_prefix, {"question": chat, "look": explore})
    loaded = await load_conversation(_CONVERSATION)
    assert loaded is not None

    # The main agent's request and the subagent's share one budget, which ends the turn.
    assert any(chunk["type"] == "error" for chunk in chunks)
    assert loaded.usage.requests == 3


@pytest.mark.parametrize(
    ("mode", "agent"), [("interactive", "explore"), ("write", "write")]
)
async def test_a_subagent_is_offered_no_withheld_tool(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix, mode: Mode, agent: str
) -> None:
    withheld = frozenset(
        {"grep", "list_documents", "delete_documents", "write_document"}
    )
    offered: set[str] = set()

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name=agent, task="look")

        return _echo(messages)

    def subagent(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.update(tool.name for tool in info.function_tools)

        return _text("done")

    deps = replace(run_prefix.deps, disabled_tools=withheld)
    await _turn(
        monkeypatch,
        replace(run_prefix, deps=deps),
        {"question": chat, "look": subagent},
        mode=mode,
    )

    assert offered
    assert not offered & withheld


def _subagents(
    run_prefix: RunPrefix, *withheld: str, confined: bool = False
) -> set[str]:
    """The subagents a turn withholding *withheld* delegates to, under an allowlist if *confined*."""
    filters = {UserScope("u"): DocumentFilter(allowed=frozenset())} if confined else {}
    deps = replace(
        run_prefix.deps, disabled_tools=frozenset(withheld), document_filters=filters
    )

    return {
        str(subagent.resolved_name)
        for capability in delegation(replace(run_prefix, deps=deps))
        if isinstance(capability, SubAgents)
        for subagent in capability.agents
    }


def test_a_list_withholds_delegation_and_each_subagent(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    monkeypatch.setattr(subagents_module.settings.tools, "disabled", [])
    conversations = subagents_module.SUBAGENTS["conversations"].tools[0]
    tool_names = {
        tool_def.name for tool_def in subagents_module.static_tools(conversations)
    }

    assert {"explore", "conversations"} <= _subagents(run_prefix)
    assert _subagents(run_prefix, "delegate_task") == set()
    assert "explore" not in _subagents(run_prefix, "explore")
    assert "conversations" not in _subagents(run_prefix, *tool_names)
    assert "conversations" not in _subagents(run_prefix, confined=True)


def test_the_data_of_another_tool_is_no_transcript() -> None:
    part = ToolReturnPart("list_documents", "ok", "c1")
    part.metadata = DataChunk(type="data-tool-output", data={"documents": [1]})

    assert subagent_transcript(part) is None
