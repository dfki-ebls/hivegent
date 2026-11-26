"""Tests for the subagents of a chat turn, delegated to with pydantic-ai-harness."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from exar.tools import effect
from pydantic import TypeAdapter
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
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, FinishChunk
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_ai_harness.sub_agents import DelegationTask, SubAgents, TaskOutputPart
from starlette.requests import Request
from starlette.responses import StreamingResponse

from hivegent import conversation_files
from hivegent.agents import RunPrefix
from hivegent.agents import subagents as subagents_module
from hivegent.agents.common import AgentTools
from hivegent.agents.subagents import Subagent, delegation
from hivegent.db.conversations import (
    OwnedConversation,
    is_user_request,
    load_conversation,
)
from hivegent.delegations import TASKS_FOLDER, delegations
from hivegent.runs import Prepared, runs
from hivegent.server.routes.conversations import history, tasks, turn
from hivegent.store import User
from hivegent.tools.base import ToolFunc
from hivegent.types import ChatRequest, Mode
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


def _unanswered_prompt(messages: list[ModelMessage]) -> str | None:
    """The user's latest prompt while the model has not answered it yet, past the memory read."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.model_name is not None:
            return None

        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content

    return None


def _call(tool: str, call_id: str, **args: object) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args, tool_call_id=call_id)])


def _text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def _echo(messages: list[ModelMessage]) -> ModelResponse:
    """The text of the last result, prompt, or report an agent got."""
    last = messages[-1].parts[-1]
    assert isinstance(last, ToolReturnPart | UserPromptPart | TaskOutputPart)

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
async def _store(
    data_dir: Path, memory_store: InMemoryConversationStore
) -> AsyncIterator[None]:
    """Keep the turns in memory, and close the tasks a test left."""
    yield
    await delegations.aclose()


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    scripts: dict[str, Script],
    *,
    mode: Mode = "interactive",
    prompt: str = "question",
    quiet: bool = True,
    user: User | None = None,
) -> list[dict[str, Any]]:
    """Run the turn asking *prompt* through the chat route, returning the chunks its client receives.

    It returns once the turn ended, and if *quiet*, once the tasks it delegated
    ended and no turn they woke runs.
    """
    user = user or User("u")
    deps = replace(run_prefix.deps, mode=mode, conversation=_CONVERSATION, user=user)
    prefix = replace(run_prefix, deps=deps, model=_model(scripts))
    prefix = replace(prefix, capabilities=[*prefix.capabilities, *delegation(prefix)])

    async def build_run_prefix(*_args: object) -> RunPrefix:
        return prefix

    monkeypatch.setattr(turn, "build_run_prefix", build_run_prefix)
    message = {
        "id": str(label_id(prompt)),
        "role": "user",
        "parts": [{"type": "text", "text": prompt}],
    }
    request = ChatRequest.model_validate(
        {"id": str(_CONVERSATION.id), "messages": [message]}
    )
    response = await turn.create_conversation_chat(
        (_CONVERSATION, False),
        user,
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

    if quiet:
        await _quiet()

    return chunks


async def _quiet() -> None:
    """Wait until the conversation's tasks ended and no turn runs, the turns they woke included."""
    async with asyncio.timeout(5):
        while (
            runs.get(_CONVERSATION) is not None
            or delegations.pending(_CONVERSATION)
            or any(
                task.status == "running"
                for task in await delegations.tasks(_CONVERSATION)
            )
        ):
            await asyncio.sleep(0.01)


async def _stored() -> list[ModelMessage]:
    loaded = await load_conversation(_CONVERSATION)
    assert loaded is not None

    return list(loaded.messages)


def _register(monkeypatch: pytest.MonkeyPatch, name: str, tool: ToolFunc) -> None:
    """Offer the subagent *name*, whose one tool is *tool*."""
    monkeypatch.setitem(
        subagents_module.SUBAGENTS,  # pyright: ignore[reportArgumentType]
        name,
        Subagent(description=name, tools=(AgentTools(factory=lambda _: [tool]),)),
    )


def _waiting(released: asyncio.Event, result: str = "found it") -> ToolFunc:
    """A tool that returns *result* once *released*, so its task outlives the turn that started it."""

    @effect("read")
    async def wait() -> str:
        """Waits."""
        await released.wait()
        return result

    return wait


def _sleeping(seconds: float) -> ToolFunc:
    """A tool that returns after *seconds*."""

    @effect("read")
    async def wait() -> str:
        """Waits."""
        await asyncio.sleep(seconds)
        return "waited"

    return wait


async def _release_after_turn(released: asyncio.Event) -> None:
    """Release *released* once the turn running now ended, which its task outlives."""
    while (run := runs.get(_CONVERSATION)) is None:
        await asyncio.sleep(0.01)

    await run.wait()
    released.set()


def _wait_then_echo(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """A subagent calling ``wait`` once, then reporting what it returned."""
    if _responses(messages) == 0:
        return _call("wait", "w1")

    return _echo(messages)


async def test_a_task_outlives_its_turn_and_wakes_the_conversation(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    _register(monkeypatch, "waiter", _waiting(released))

    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools = {tool.name: tool for tool in info.function_tools}
        properties = tools["delegate_task"].parameters_json_schema["properties"]
        # A task runs in the background unless the main agent waits for it.
        assert properties["background"]["default"] is True

        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="waiter", task="look")

        return _echo(messages)

    releasing = asyncio.create_task(_release_after_turn(released))
    chunks = await _turn(
        monkeypatch, run_prefix, {"question": chat, "look": _wait_then_echo}
    )
    await releasing
    loaded = await load_conversation(_CONVERSATION)
    assert loaded is not None
    *_, reply = loaded.messages
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)
    shown = await tasks.get_conversation_task_messages(_CONVERSATION, task.id)

    # The turn ended on the receipt, and the turn the report woke continued its reply.
    assert "found it" not in json.dumps(chunks)
    assert isinstance(reply, ModelResponse) and "found it" in (reply.text or "")
    assert [is_user_request(m) for m in loaded.messages].count(True) == 1
    assert (task.agent_name, task.status, task.outcome) == ("waiter", "finished", "ok")
    assert shown[0].role == "user" and "look" in str(shown[0].parts)
    # The receipt, the report, and the subagent's two requests count into the conversation.
    assert loaded.usage.requests == 5

    async with delegations.turn(_CONVERSATION, User("u"), turn._wake):
        await tasks.stop_conversation_task(_CONVERSATION, task.id)
        [finished] = await tasks.list_conversation_tasks(_CONVERSATION)
        assert not finished.user_stopped


async def test_teams_receives_an_auto_backgrounded_report_before_finish(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    monkeypatch.setattr(subagents_module, "_AUTO_BACKGROUND_SECONDS", 0.01)
    _register(monkeypatch, "waiter", _waiting(released))

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="waiter", task="look", background=False)

        released.set()

        return _echo(messages)

    chunks = await _turn(
        monkeypatch,
        run_prefix,
        {"question": chat, "look": _wait_then_echo},
        mode="read",
        user=User("u", kind="service"),
    )

    finish = next(i for i, chunk in enumerate(chunks) if chunk["type"] == "finish")
    assert "found it" in json.dumps(chunks[:finish])
    assert await tasks.list_conversation_tasks(_CONVERSATION) == []


async def test_deleting_wakes_no_turn_for_a_report_its_cancelled_turn_left(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    woke = asyncio.Event()
    _register(monkeypatch, "waiter", _waiting(released))

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="waiter", task="look")

        return _echo(messages)

    await _turn(monkeypatch, run_prefix, {"question": chat, "look": _wait_then_echo}, quiet=False)
    opened = asyncio.Event()

    async def stream() -> AsyncIterator[BaseChunk]:
        # The cancelled turn ends only once the report waits, which would wake the conversation.
        async with delegations.turn(_CONVERSATION, User("u"), lambda *_: woke.set()) as owner:
            try:
                opened.set()
                await asyncio.Future[None]()
            finally:
                released.set()

                while not owner.reports(conversation_id=_CONVERSATION.key):
                    await asyncio.sleep(0)

        yield FinishChunk()

    async def prepare() -> Prepared:
        return Prepared(stream(), [])

    async def remove(_conversation: OwnedConversation) -> bool:
        return True

    monkeypatch.setattr(conversation_files, "remove_conversation", remove)
    _ = runs.start(_CONVERSATION, User("u"), None, prepare())
    await opened.wait()

    assert await conversation_files.delete_conversation(_CONVERSATION)
    assert not woke.is_set() and runs.get(_CONVERSATION) is None


async def test_usage_spent_while_a_turn_is_stored_goes_to_the_next() -> None:
    usage = RunUsage(requests=1, details={"cached": 1})

    async with delegations.turn(_CONVERSATION, User("u"), lambda *_: None):
        total, stored = delegations.usage_for_save(_CONVERSATION, usage)
        usage.incr(RunUsage(requests=1, details={"cached": 1}))
        stored()
        late, _ = delegations.usage_for_save(_CONVERSATION, RunUsage())

    assert total == late == RunUsage(requests=1, details={"cached": 1})


async def test_tasks_show_interrupted_records_before_the_next_turn(data_dir: Path) -> None:
    folder = _CONVERSATION.folder(data_dir) / TASKS_FOLDER
    folder.mkdir(parents=True)
    task = DelegationTask(
        id=uuid4().hex, agent_name="explore", prompt="look", conversation_id=_CONVERSATION.key
    )
    (folder / f"{task.id}.json").write_bytes(TypeAdapter(DelegationTask).dump_json(task))

    [shown] = await tasks.list_conversation_tasks(_CONVERSATION)

    assert (shown.status, shown.outcome) == ("finished", "cancelled")


async def test_a_waited_task_reports_in_the_same_turn(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call(
                "delegate_task",
                "d1",
                agent_name="explore",
                task="look",
                background=False,
            )

        return _echo(messages)

    chunks = await _turn(
        monkeypatch,
        run_prefix,
        {"question": chat, "look": lambda _messages, _info: _text("found it")},
    )
    stored = await _stored()

    # The report is the call's result, in the turn's own stream, and no report follows as a prompt.
    assert "found it" in json.dumps(chunks)
    assert any(
        isinstance(part, ToolReturnPart) and "found it" in str(part.content)
        for message in stored
        for part in message.parts
    )
    assert not any(
        isinstance(part, TaskOutputPart) for message in stored for part in message.parts
    )


async def test_a_waited_task_moves_to_the_background_in_time(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    monkeypatch.setattr(subagents_module, "_AUTO_BACKGROUND_SECONDS", 0.1)
    _register(monkeypatch, "waiter", _waiting(released))

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call(
                "delegate_task",
                "d1",
                agent_name="waiter",
                task="look",
                background=False,
            )

        return _echo(messages)

    releasing = asyncio.create_task(_release_after_turn(released))
    chunks = await _turn(
        monkeypatch, run_prefix, {"question": chat, "look": _wait_then_echo}
    )
    await releasing
    *_, reply = await _stored()

    # The turn stops waiting and ends, and the report wakes the conversation.
    assert "found it" not in json.dumps(chunks)
    assert isinstance(reply, ModelResponse) and "found it" in (reply.text or "")


async def test_a_later_turn_steers_a_running_task(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    task_ids: list[str] = []
    _register(monkeypatch, "looper", _sleeping(0.02))

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        match _unanswered_prompt(messages):
            case "question":
                return _call("delegate_task", "d1", agent_name="looper", task="loop")
            case "steer":
                return _call(
                    "message_task", "m1", task_id=task_ids[0], message="stop now"
                )
            case _:
                return _echo(messages)

    def looper(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if "stop now" in str(messages):
            return _text("stopped as told")

        return _call("wait", f"w{_responses(messages)}")

    scripts: dict[str, Script] = {"question": chat, "loop": looper}
    await _turn(monkeypatch, run_prefix, scripts, quiet=False)
    task_ids.extend(
        task.id for task in await tasks.list_conversation_tasks(_CONVERSATION)
    )
    await _turn(monkeypatch, run_prefix, scripts, prompt="steer")
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)

    # The turn after the one that delegated steers the task it did not start.
    assert (task.outcome, task.output) == ("ok", "stopped as told")


async def test_a_subagent_reports_after_its_steps(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    monkeypatch.setattr(subagents_module.settings.subagents, "max_steps", 2)

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="explore", task="look")

        return _echo(messages)

    def explore(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if "Report your findings now" in str(messages[-1]):
            return _text("what I found so far")

        return _call("list_documents", f"l{_responses(messages)}")

    await _turn(monkeypatch, run_prefix, {"question": chat, "look": explore})
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)

    assert (task.outcome, task.output) == ("ok", "what I found so far")


@pytest.mark.parametrize(
    ("timeout", "answer"),
    [(1.0, "partial findings"), (0.2, "exceeded")],
)
async def test_a_subagent_out_of_time_reports(
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    timeout: float,
    answer: str,
) -> None:
    """Past the soft deadline its calls are refused and it reports, past the timeout it is cut off.

    The deadline changes neither its tools nor its instructions, so every request it sends has the same prefix.
    """

    monkeypatch.setattr(subagents_module.settings.subagents, "timeout_seconds", timeout)
    _register(monkeypatch, "sleeper", _sleeping(0.85))

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


async def test_a_report_is_no_prompt_of_the_user(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        match _responses(messages):
            case 0:
                return _call("delegate_task", "d1", agent_name="explore", task="look")
            case 1:
                return _text("waiting")
            case _:
                return _echo(messages)

    monkeypatch.setattr(subagents_module.settings.llm, "tool_output_max_chars", 300)
    await _turn(
        monkeypatch,
        run_prefix,
        {
            "question": chat,
            "look": lambda _messages, _info: _text("found it" + "x" * 1000),
        },
    )
    stored = await _stored()
    shown = await history.active_messages(_CONVERSATION)

    # The report reaches the main agent, bounded like a tool return, but neither as a user turn nor on screen.
    assert "found it" in str(stored[-2].parts)
    assert "Cut short of its" in str(stored[-2].parts)
    assert "x" * 300 not in str(stored[-2].parts)
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

    _register(monkeypatch, "mailer", send)

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
    assert "cannot ask the user" in str(await _stored())
    assert all(chunk["type"] != "tool-approval-request" for chunk in chunks)


async def test_the_turn_budget_bounds_the_tasks_it_started(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    requests: list[int] = []
    _register(monkeypatch, "looper", _waiting(released, "waited"))

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name="looper", task="loop")

        released.set()

        return _text("waiting")

    def looper(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        requests.append(_responses(messages))

        return _call("wait", f"w{len(requests)}")

    monkeypatch.setattr(turn, "turn_usage_limits", UsageLimits(request_limit=4))
    await _turn(monkeypatch, run_prefix, {"question": chat, "loop": looper})
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)

    # The turn's two requests leave its task two, though the turn ended before it.
    assert len(requests) == 2
    assert task.outcome != "ok"


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

    monkeypatch.setattr(subagents_module.settings.tools, "disabled", sorted(withheld))
    await _turn(
        monkeypatch, run_prefix, {"question": chat, "look": subagent}, mode=mode
    )

    assert offered
    assert not offered & withheld


def test_a_list_withholds_delegation_and_each_subagent(
    monkeypatch: pytest.MonkeyPatch, run_prefix: RunPrefix
) -> None:
    conversations = subagents_module.SUBAGENTS["conversations"].tools[0]
    tool_names = {
        tool_def.name for tool_def in subagents_module.static_tools(conversations)
    }

    def offered(*withheld: str) -> set[str]:
        """The subagents a turn delegates to while the operator withholds *withheld*."""
        monkeypatch.setattr(subagents_module.settings.tools, "disabled", list(withheld))

        return {
            str(subagent.resolved_name)
            for capability in delegation(run_prefix)
            if isinstance(capability, SubAgents)
            for subagent in capability.agents
        }

    assert {"explore", "conversations"} <= offered()
    assert offered("delegate_task") == set()
    assert "explore" not in offered("explore")
    assert "conversations" not in offered(*tool_names)
