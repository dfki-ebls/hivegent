"""Tests for the subagents of a chat turn, delegated to with pydantic-ai-harness."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

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
from hivegent.agents.subagents import SUBAGENTS, Subagent, delegation
from hivegent.db.conversations import is_user_request, load_conversation
from hivegent.delegations import TASKS_FOLDER, delegations
from hivegent.runs import Prepared, runs
from hivegent.server.routes.conversations import history, tasks, turn
from hivegent.store import User
from hivegent.tools.base import ToolFunc
from hivegent.types import ChatRequest, Mode
from tests.helpers import label_id, owned, sse_chunks

type Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]
"""Answers an agent."""

type UsePrefix = Callable[[RunPrefix], None]
type Configure = Callable[..., None]

_CONVERSATION = owned("subagents")

_TASK_ID = "a" * 32


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


def _delegating(agent: str, **args: object) -> Script:
    """A main agent delegating once to *agent* with the prompt ``look``, then echoing what it got back."""

    def chat(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("delegate_task", "d1", agent_name=agent, task="look", **args)

        return _echo(messages)

    return chat


def _answering(text: str) -> Script:
    def answer(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return _text(text)

    return answer


def _wait_then_echo(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """A subagent calling ``wait`` once, then reporting what it returned."""
    if _responses(messages) == 0:
        return _call("wait", "w1")

    return _echo(messages)


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
    use_prefix: UsePrefix,
    run_prefix: RunPrefix,
    scripts: dict[str, Script],
    *,
    subagents: Mapping[str, Subagent] = SUBAGENTS,
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
    prefix = replace(
        prefix, capabilities=[*prefix.capabilities, *delegation(prefix, subagents)]
    )
    use_prefix(prefix)
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
    """Return once the conversation's task owner closed, which it does once no turn, task, or report remains."""
    await delegations.idle(_CONVERSATION)

    if (run := runs.get(_CONVERSATION)) is not None:
        await run.wait()


async def _stored() -> list[ModelMessage]:
    loaded = await load_conversation(_CONVERSATION)
    assert loaded is not None

    return list(loaded.messages)


def _offering(
    name: str, tool: ToolFunc, clock: Callable[[], float] = time.monotonic
) -> Mapping[str, Subagent]:
    """The subagents with the one *name*, whose one tool is *tool*, its report deadline counted on *clock*."""
    return {
        **SUBAGENTS,
        name: Subagent(
            description=name,
            tools=(AgentTools(factory=lambda _: [tool]),),
            clock=clock,
        ),
    }


@pytest.fixture()
def instant_background(configure: Configure) -> None:
    """Move a task the main agent waits for to the background as soon as it waits."""
    configure(subagents={"auto_background_seconds": 0})


def _waiting(released: asyncio.Event, result: str = "found it") -> ToolFunc:
    """A tool that returns *result* once *released*, so its task outlives the turn that started it."""

    @effect("read")
    async def wait() -> str:
        """Waits."""
        await released.wait()
        return result

    return wait


@pytest.mark.parametrize(
    "args", [{}, {"background": False}], ids=["in-the-background", "waited-for"]
)
@pytest.mark.usefixtures("instant_background")
async def test_a_task_outlives_its_turn_and_wakes_the_conversation(
    use_prefix: UsePrefix, run_prefix: RunPrefix, args: dict[str, bool]
) -> None:
    """A task the main agent waits for moves to the background, and the turn ends without it."""
    released = asyncio.Event()
    subagents = _offering("waiter", _waiting(released))
    delegate = _delegating("waiter", **args)

    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools = {tool.name: tool for tool in info.function_tools}
        properties = tools["delegate_task"].parameters_json_schema["properties"]
        # A task runs in the background unless the main agent waits for it.
        assert properties["background"]["default"] is True

        return delegate(messages, info)

    chunks = await _turn(
        use_prefix,
        run_prefix,
        {"question": chat, "look": _wait_then_echo},
        subagents=subagents,
        quiet=False,
    )
    released.set()
    await _quiet()
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


@pytest.mark.usefixtures("instant_background")
async def test_teams_receives_an_auto_backgrounded_report_before_finish(
    use_prefix: UsePrefix, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    subagents = _offering("waiter", _waiting(released))
    delegate = _delegating("waiter", background=False)

    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _responses(messages) > 0:
            released.set()

        return delegate(messages, info)

    chunks = await _turn(
        use_prefix,
        run_prefix,
        {"question": chat, "look": _wait_then_echo},
        subagents=subagents,
        mode="read",
        user=User("u", kind="service"),
    )

    finish = next(i for i, chunk in enumerate(chunks) if chunk["type"] == "finish")
    assert "found it" in json.dumps(chunks[:finish])
    assert await tasks.list_conversation_tasks(_CONVERSATION) == []


@pytest.mark.usefixtures("conversation_rows")
async def test_deleting_wakes_no_turn_for_a_report_its_cancelled_turn_left(
    use_prefix: UsePrefix, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    woke = asyncio.Event()
    opened = asyncio.Event()
    subagents = _offering("waiter", _waiting(released))

    await _turn(
        use_prefix,
        run_prefix,
        {"question": _delegating("waiter"), "look": _wait_then_echo},
        subagents=subagents,
        quiet=False,
    )

    async def stream() -> AsyncIterator[BaseChunk]:
        async with delegations.turn(
            _CONVERSATION, User("u"), lambda *_: woke.set()
        ) as owner:
            try:
                opened.set()
                await asyncio.Future[None]()
            finally:
                # The cancelled turn ends only once the report waits, which would wake the conversation.
                released.set()
                await owner.wait_children(None)

        yield FinishChunk()

    async def prepare() -> Prepared:
        return Prepared(stream(), [])

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


async def test_tasks_show_interrupted_records_before_the_next_turn(
    data_dir: Path,
) -> None:
    folder = _CONVERSATION.folder(data_dir) / TASKS_FOLDER
    folder.mkdir(parents=True)
    task = DelegationTask(
        id=_TASK_ID,
        agent_name="explore",
        prompt="look",
        conversation_id=_CONVERSATION.key,
    )
    (folder / f"{_TASK_ID}.json").write_bytes(
        TypeAdapter(DelegationTask).dump_json(task)
    )

    [shown] = await tasks.list_conversation_tasks(_CONVERSATION)

    assert (shown.status, shown.outcome) == ("finished", "cancelled")


async def test_a_waited_task_reports_in_the_same_turn(
    use_prefix: UsePrefix, run_prefix: RunPrefix
) -> None:
    chunks = await _turn(
        use_prefix,
        run_prefix,
        {
            "question": _delegating("explore", background=False),
            "look": _answering("found it"),
        },
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


async def test_a_later_turn_steers_a_running_task(
    use_prefix: UsePrefix, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    task_ids: list[str] = []
    subagents = _offering("looper", _waiting(released))
    delegate = _delegating("looper")

    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        match _unanswered_prompt(messages):
            case "question":
                return delegate(messages, info)
            case "steer":
                return _call(
                    "message_task", "m1", task_id=task_ids[0], message="stop now"
                )
            case _:
                # The task reads the message once its call returns.
                if "stop now" in str(messages):
                    released.set()

                return _echo(messages)

    def looper(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if "stop now" in str(messages):
            return _text("stopped as told")

        return _call("wait", "w1")

    scripts: dict[str, Script] = {"question": chat, "look": looper}
    await _turn(use_prefix, run_prefix, scripts, subagents=subagents, quiet=False)
    task_ids.extend(
        task.id for task in await tasks.list_conversation_tasks(_CONVERSATION)
    )
    await _turn(use_prefix, run_prefix, scripts, subagents=subagents, prompt="steer")
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)

    # The turn after the one that delegated steers the task it did not start.
    assert (task.outcome, task.output) == ("ok", "stopped as told")


@pytest.mark.parametrize(
    ("max_steps", "advance"), [(2, 0.0), (50, 100.0)], ids=["steps", "time"]
)
async def test_a_subagent_reports_once_its_budget_is_used_up(
    use_prefix: UsePrefix,
    run_prefix: RunPrefix,
    configure: Configure,
    max_steps: int,
    advance: float,
) -> None:
    """Its calls are refused and it reports, under the same tools and instructions it worked with."""
    now = [0.0]
    configure(subagents={"max_steps": max_steps, "timeout_seconds": 100})

    @effect("read")
    async def work() -> str:
        """Works, for as long as the test says on its clock."""
        now[0] += advance

        return "worked"

    subagents = _offering("worker", work, lambda: now[0])
    prefixes: list[tuple[str | None, list[ToolDefinition]]] = []

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prefixes.append((info.instructions, info.function_tools))

        if "Report your findings now" in str(messages[-1]):
            return _text("partial findings")

        return _call("work", f"w{len(prefixes)}")

    await _turn(
        use_prefix,
        run_prefix,
        {"question": _delegating("worker"), "look": worker},
        subagents=subagents,
    )
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)

    assert (task.outcome, task.output) == ("ok", "partial findings")
    assert len(prefixes) == 3
    assert all(prefix == prefixes[0] for prefix in prefixes)


async def test_a_subagent_past_its_timeout_is_cut_off(
    use_prefix: UsePrefix, run_prefix: RunPrefix, configure: Configure
) -> None:
    configure(subagents={"timeout_seconds": 0.01})
    # The clock stands still, so the soft deadline never precedes the timeout.
    subagents = _offering("stuck", _waiting(asyncio.Event()), lambda: 0.0)

    await _turn(
        use_prefix,
        run_prefix,
        {"question": _delegating("stuck"), "look": _wait_then_echo},
        subagents=subagents,
    )
    *_, reply = await _stored()

    assert isinstance(reply, ModelResponse) and "exceeded" in (reply.text or "")


async def test_a_report_is_no_prompt_of_the_user(
    use_prefix: UsePrefix, run_prefix: RunPrefix, configure: Configure
) -> None:
    delegate = _delegating("explore")

    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 1:
            return _text("waiting")

        return delegate(messages, info)

    configure(llm={"tool_output_max_chars": 300})
    await _turn(
        use_prefix,
        run_prefix,
        {"question": chat, "look": _answering("found it" + "x" * 1000)},
    )
    stored = await _stored()
    shown = await history.active_messages(_CONVERSATION)

    # The report reaches the main agent, bounded like a tool return, but neither as a user turn nor on screen,
    # and the turn it woke answers it in a reply of its own.
    assert "found it" in str(stored[-2].parts)
    assert "Cut short of its" in str(stored[-2].parts)
    assert "x" * 300 not in str(stored[-2].parts)
    assert [is_user_request(message) for message in stored].count(True) == 1
    assert [message.role for message in shown] == ["user", "assistant", "assistant"]


async def test_a_subagent_refuses_what_would_ask(
    use_prefix: UsePrefix, run_prefix: RunPrefix
) -> None:
    sent: list[str] = []

    @effect("execute")
    async def send(text: str) -> str:
        """Sends a text."""
        sent.append(text)
        return f"sent {text}"

    subagents = _offering("mailer", send)

    def mailer(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return _call("send", "s1", text="hi")

        return _echo(messages)

    chunks = await _turn(
        use_prefix,
        run_prefix,
        {"question": _delegating("mailer"), "look": mailer},
        subagents=subagents,
        mode="write",
    )

    # The write mode asks before any execution, which a subagent refuses rather than asking.
    assert sent == []
    assert "cannot ask the user" in str(await _stored())
    assert all(chunk["type"] != "tool-approval-request" for chunk in chunks)


async def test_the_turn_budget_bounds_the_tasks_it_started(
    monkeypatch: pytest.MonkeyPatch, use_prefix: UsePrefix, run_prefix: RunPrefix
) -> None:
    released = asyncio.Event()
    requests: list[int] = []
    subagents = _offering("looper", _waiting(released, "waited"))
    delegate = _delegating("looper")

    def chat(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _responses(messages) == 0:
            return delegate(messages, info)

        released.set()

        return _text("waiting")

    def looper(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        requests.append(_responses(messages))

        return _call("wait", f"w{len(requests)}")

    monkeypatch.setattr(turn, "turn_usage_limits", UsageLimits(request_limit=4))
    await _turn(
        use_prefix,
        run_prefix,
        {"question": chat, "look": looper},
        subagents=subagents,
    )
    [task] = await tasks.list_conversation_tasks(_CONVERSATION)

    # The turn's two requests leave its task two, though the turn ended before it.
    assert len(requests) == 2
    assert task.outcome != "ok"


@pytest.mark.parametrize(
    ("mode", "agent"), [("interactive", "explore"), ("write", "write")]
)
async def test_a_subagent_is_offered_no_withheld_tool(
    use_prefix: UsePrefix,
    run_prefix: RunPrefix,
    configure: Configure,
    mode: Mode,
    agent: str,
) -> None:
    withheld = frozenset(
        {"grep", "list_documents", "delete_documents", "write_document"}
    )
    offered: set[str] = set()

    def subagent(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.update(tool.name for tool in info.function_tools)

        return _text("done")

    configure(tools={"disabled": sorted(withheld)})
    await _turn(
        use_prefix,
        run_prefix,
        {"question": _delegating(agent), "look": subagent},
        mode=mode,
    )

    assert offered
    assert not offered & withheld


def test_a_list_withholds_delegation_and_each_subagent(
    run_prefix: RunPrefix, configure: Configure
) -> None:
    conversations = subagents_module.SUBAGENTS["conversations"].tools[0]
    tool_names = {
        tool_def.name for tool_def in subagents_module.static_tools(conversations)
    }

    def offered(*withheld: str) -> set[str]:
        """The subagents a turn delegates to while the operator withholds *withheld*."""
        configure(tools={"disabled": list(withheld)})

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
