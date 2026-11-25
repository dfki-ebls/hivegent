"""The agent's tools served over MCP, from a client's side of the connection."""

from collections.abc import Sequence
from pathlib import Path

import pytest
from exar.mcp import Confirmations, server
from exar.tools import effect
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitRequestParams
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from mcp.types import ContentBlock, TextContent
from pydantic_ai import ModelRetry

from hivegent.agents import capabilities
from hivegent.agents.approval import APPROVED_CHANGES, changes_digest
from hivegent.mcp import adapter
from hivegent.plugins import api
from hivegent.store import User
from hivegent.tools.changes import Changeset
from hivegent.workspace import Gateway


@pytest.fixture(autouse=True)
def user(data_dir: Path) -> User:
    """Every call is made as ``u``, whose workspace holds one note."""
    user = User("u")
    workspace = user.home.workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_text("needle\n")

    return user


def _served(user: User) -> FastMCP:
    """The agent's tools, every call made as *user*."""

    async def caller(_token: AccessToken | None) -> User:
        return user

    confirmations = Confirmations(key=b"test")

    return server(
        adapter.agent_tools(caller, confirmations),
        name="probe",
        policy=adapter.POLICY,
        confirmations=confirmations,
    )


def _text(blocks: Sequence[ContentBlock]) -> str:
    block = blocks[0]
    assert isinstance(block, TextContent)

    return block.text


async def test_a_read_tool_runs_as_the_agent_s_and_tmp_is_out_of_reach(
    user: User,
) -> None:
    async with Client(_served(user)) as client:
        names = {tool.name for tool in await client.list_tools()}
        result = await client.call_tool("grep", {"pattern": "needle"})

        with pytest.raises(ToolError, match="This tool addresses ~"):
            await client.call_tool(
                "write_document", {"file_path": "/tmp/a.md", "content": "x"}
            )

    assert {"grep", "read_document", "write_document"} <= names
    assert "apply_changes" not in names
    assert "~/notes.md" in _text(result.content)


async def test_a_write_asks_then_applies_on_the_confirmed_retry(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    asked: list[str] = []
    applied: list[str] = []

    async def _commit(_gateway: Gateway, changeset: Changeset[str]) -> str:
        applied.extend(changeset.locations)

        return "written"

    async def _answer(
        message: str, _type: object, _params: ElicitRequestParams, _ctx: object
    ) -> bool:
        asked.append(message)

        return len(asked) > 1

    monkeypatch.setattr(Gateway, "commit", _commit)
    args = {"file_path": "~/report.md", "content": "hello"}

    async with Client(_served(user), elicitation_handler=_answer) as client:
        with pytest.raises(ToolError, match="denied by the user"):
            await client.call_tool("write_document", args)

        result = await client.call_tool("write_document", args)

    assert applied == ["~/report.md"]
    assert "- create ~/report.md" in asked[1] and "+hello" in asked[1]
    assert _text(result.content) == "written"


async def test_a_withheld_tool_is_neither_listed_nor_called(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    monkeypatch.setattr(adapter.settings.tools, "disabled", ["grep"])

    async with Client(_served(user)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert "grep" not in names
    assert "read_document" in names

    with pytest.raises(KeyError):
        await adapter.invoke_agent_tool("grep", {"pattern": "x"}, adapter._deps(user))


async def test_a_sandbox_only_tool_is_not_listed(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    """Only a program is handed it, and a client is none."""
    monkeypatch.setattr(adapter.settings.tools, "sandbox_only", ["search"])

    async with Client(_served(user)) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert "search" not in names
    assert "read_document" in names


async def test_an_approval_holds_only_for_the_changes_it_was_given_for(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    applied: list[str] = []

    async def _commit(_gateway: Gateway, changeset: Changeset[str]) -> str:
        applied.extend(changeset.locations)

        return "written"

    monkeypatch.setattr(Gateway, "commit", _commit)
    args = {"file_path": "~/report.md", "content": "hello"}

    with pytest.raises(ModelRetry, match="no longer the ones the user approved"):
        await adapter.invoke_agent_tool(
            "write_document",
            args,
            adapter._deps(user),
            approved=True,
            metadata={APPROVED_CHANGES: changes_digest(None)},
        )

    assert applied == []


async def test_a_plugin_tool_is_served_with_the_hints_of_its_effect(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    @effect("execute")
    async def lookup_send(term: str) -> str:
        """Send a term."""
        return term

    feature = capabilities._plugin_feature(
        "lookup", api.Feature(id="lookup", tools=[lookup_send])
    )
    monkeypatch.setattr(adapter, "PLUGIN_FEATURE_IDS", frozenset({"lookup"}))
    monkeypatch.setattr(adapter, "FEATURES", (feature,))
    app = _served(user)

    (tool,) = await app.list_tools()

    assert tool.name == "lookup_send"
    assert tool.annotations is not None
    assert tool.annotations.open_world_hint
    assert not tool.annotations.read_only_hint
