"""The agent's tools served over MCP, from a client's side of the connection."""

from collections.abc import Sequence
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitRequestParams
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from mcp.types import ContentBlock, TextContent

from hivegent.changes import Changeset
from hivegent.config import settings
from hivegent.mcp import adapter, mcp_app
from hivegent.store import User
from hivegent.workspace import Gateway


@pytest.fixture(autouse=True)
def user(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> User:
    """Every call is made as ``u``, whose workspace holds one note."""
    user = User("u")
    workspace = user.store.workspace_dir(data_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_text("needle\n")

    async def _user(_token: AccessToken | None) -> User:
        return user

    monkeypatch.setattr(adapter, "get_mcp_user", _user)

    return user


def _text(blocks: Sequence[ContentBlock]) -> str:
    block = blocks[0]
    assert isinstance(block, TextContent)

    return block.text


async def test_a_read_tool_runs_as_the_agent_s_and_tmp_is_out_of_reach() -> None:
    async with Client(mcp_app) as client:
        names = {tool.name for tool in await client.list_tools()}
        result = await client.call_tool("grep", {"pattern": "needle"})

        with pytest.raises(ToolError, match="This tool addresses ~"):
            await client.call_tool(
                "write_document", {"file_path": "/tmp/a.md", "content": "x"}
            )

    assert {"grep", "read_document", "write_document"} <= names
    assert "apply_changes" not in names
    assert "~/notes.md" in _text(result.content)


@pytest.mark.parametrize("model", ["", "main-model"])
async def test_explore_is_served_only_with_a_main_model(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    monkeypatch.setattr(settings.llm, "model", model)
    app = FastMCP("probe")
    adapter.serve_agent_tools(app)

    names = {tool.name for tool in await app.list_tools()}

    assert "grep" in names
    assert ("explore" in names) == bool(model)


async def test_a_write_asks_then_applies_on_the_confirmed_retry(
    monkeypatch: pytest.MonkeyPatch,
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

    async with Client(mcp_app, elicitation_handler=_answer) as client:
        with pytest.raises(ToolError, match="denied by the user"):
            await client.call_tool("write_document", args)

        result = await client.call_tool("write_document", args)

    assert applied == ["~/report.md"]
    assert "- create ~/report.md" in asked[1] and "+hello" in asked[1]
    assert _text(result.content) == "written"
