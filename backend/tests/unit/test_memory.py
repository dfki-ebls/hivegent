"""Memory: edited in place, approved before a write, and reported when a conversation or compaction window starts."""

import pytest
from exar.memory import InMemoryMemory
from exar.tools import Retry
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents import invoke_agent_tool
from hivegent.agents.common import UserDeps
from hivegent.agents.tools import memory
from hivegent.agents.tools.memory import MEMORY_NOTE, edit_memory, memory_toolset
from hivegent.store import User


def test_an_edit_names_one_passage_and_fits() -> None:
    """A miss or an ambiguous match would edit the wrong passage, so both refuse."""
    with pytest.raises(Retry, match="occurs 0 times"):
        edit_memory("- tea", "- coffee", "- milk", 100)

    with pytest.raises(Retry, match="occurs 2 times"):
        edit_memory("- tea\n- tea", "- coffee", "- tea", 100)

    with pytest.raises(Retry, match="at most 10"):
        edit_memory("- tea", "- a longer line", None, 10)

    # An oversized memory may still shrink, or it could never be condensed.
    assert edit_memory("a" * 10 + "b" * 10, "c" * 5, "b" * 10, 10) == "a" * 10 + "c" * 5


def _reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="ok")])


async def test_the_memory_is_reported_once_per_window() -> None:
    """Each turn extends the history, so only a compaction calls for a second report."""
    agent = Agent(deps_type=UserDeps, toolsets=[memory_toolset.toolset()])
    history: list[ModelMessage] = []

    for compacted in (False, False, True):
        if compacted:
            history.append(ModelResponse(parts=[CompactionPart(content="the gist")]))

        result = await agent.run(
            "hi",
            model=FunctionModel(_reply),
            deps=UserDeps(user=User("u"), mode="interactive"),
            message_history=history,
            capabilities=[MEMORY_NOTE],
        )
        history = result.all_messages()

    assert [
        part.content
        for message in history
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "read_memory"
    ] == ["- likes tea", "- likes tea"]


@pytest.fixture()
def page(monkeypatch: pytest.MonkeyPatch) -> InMemoryMemory[str, str]:
    """The memory of ``u``, which every call reads and writes in place of the database's."""
    page = InMemoryMemory[str, str](entries={"u": "- tea"})
    monkeypatch.setattr(memory, "user_memory", lambda _user: page)

    return page


async def test_write_memory_asks_even_in_write_mode(
    page: InMemoryMemory[str, str],
) -> None:
    """A write outside the gateway is reviewed in every mode, and only an approved call saves."""
    deps = UserDeps(user=User(id="u"), mode="write")
    args = {"content": "- green"}

    with pytest.raises(ApprovalRequired):
        await invoke_agent_tool("write_memory", args, deps)

    assert await page.read(["u"]) == {"u": "- tea"}

    await invoke_agent_tool("write_memory", args, deps, approved=True)

    assert await page.read(["u"]) == {"u": "- tea\n- green"}
