"""Memory: edited in place and reported when a conversation or compaction window starts."""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents.common import UserDeps
from hivegent.agents.tools.memory import MemoryNote, edit_memory, memory_toolset
from hivegent.store import User
from hivegent.tools.base import ToolRetry


def test_an_edit_names_one_passage_and_fits() -> None:
    """A miss or an ambiguous match would edit the wrong passage, so both refuse."""
    with pytest.raises(ToolRetry, match="does not occur"):
        edit_memory("- tea", "- coffee", "- milk", 100)

    with pytest.raises(ToolRetry, match="occurs 2 times"):
        edit_memory("- tea\n- tea", "- coffee", "- tea", 100)

    with pytest.raises(ToolRetry, match="at most 10"):
        edit_memory("- tea", "- a longer line", None, 10)

    # An oversized memory may still shrink, or it could never be condensed.
    assert edit_memory("a" * 10 + "b" * 10, "c" * 5, "b" * 10, 10) == "a" * 10 + "c" * 5


def _reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="ok")])


async def test_the_memory_is_reported_once_per_window() -> None:
    """Each turn extends the history, so only a compaction calls for a second report."""
    agent = Agent(deps_type=UserDeps, toolsets=[memory_toolset])
    history: list[ModelMessage] = []

    for compacted in (False, False, True):
        if compacted:
            history.append(ModelResponse(parts=[CompactionPart(content="the gist")]))

        result = await agent.run(
            "hi",
            model=FunctionModel(_reply),
            deps=UserDeps(user=User("u"), mode="interactive"),
            message_history=history,
            capabilities=[MemoryNote()],
        )
        history = result.all_messages()

    assert [
        part.content
        for message in history
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "read_memory"
    ] == ["- likes tea", "- likes tea"]
