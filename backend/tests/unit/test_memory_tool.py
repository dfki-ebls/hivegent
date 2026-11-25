"""Tests for the approval every memory write asks for."""

import pytest
from exar.memory import InMemoryMemory
from pydantic_ai.exceptions import ApprovalRequired

from hivegent.agents import invoke_agent_tool
from hivegent.agents.common import UserDeps
from hivegent.agents.tools import memory
from hivegent.store import User


async def test_write_memory_asks_even_in_write_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write outside the gateway is reviewed in every mode, and only an approved call saves."""
    page = InMemoryMemory[str, str](entries={"u": "- tea"})
    monkeypatch.setattr(memory, "user_memory", lambda _user: page)
    deps = UserDeps(user=User(id="u"), mode="write")
    args = {"content": "- green"}

    with pytest.raises(ApprovalRequired):
        await invoke_agent_tool("write_memory", args, deps)
    assert await page.read(["u"]) == {"u": "- tea"}

    await invoke_agent_tool("write_memory", args, deps, approved=True)
    assert await page.read(["u"]) == {"u": "- tea\n- green"}
