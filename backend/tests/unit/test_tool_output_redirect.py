"""Tests for redirecting a tool's result into a workspace file.

A tool built with a writer takes an ``output_path``: the suffix picks which of
the result's two channels is stored, and the model is handed a receipt instead
of the result. The write itself goes through the ordinary document gateway, so
these tests stub only that, not the tools around it.
"""

from pathlib import Path

import pytest

from hivegent import workspace_events
from hivegent.tools.base import ToolRetry
from hivegent.tools.documents import GlobDocumentsTool
from hivegent.tools.mutations import WriteDocumentTool
from hivegent.tools.sink import RedirectedOutput
from hivegent.workspace_events import announcing_mutator


@pytest.fixture()
def written() -> dict[str, str]:
    """Collect what the write gateway was asked to persist."""
    return {}


@pytest.fixture()
def writer(tmp_path: Path, written: dict[str, str]) -> WriteDocumentTool:
    """A writer over *tmp_path* that records instead of touching the store."""

    async def mutator(
        path: str, content: str, mode: str, expected_hash: str | None
    ) -> str:
        _ = mode, expected_hash
        written[path] = content
        return f"wrote {path}"

    return WriteDocumentTool(paths=tmp_path, mutator=mutator)


@pytest.fixture()
def tool(tmp_path: Path, writer: WriteDocumentTool) -> GlobDocumentsTool:
    (tmp_path / "a.md").write_text("one")
    (tmp_path / "b.md").write_text("two")
    return GlobDocumentsTool(paths=tmp_path, writer=writer)


async def test_json_stores_the_structured_result(
    tool: GlobDocumentsTool, written: dict[str, str]
) -> None:
    out = await tool("*.md", output_path=".scratch/hits.json")

    assert written[".scratch/hits.json"] == '["a.md","b.md"]'
    assert out.data == RedirectedOutput(
        output_path=".scratch/hits.json",
        format="json",
        characters=len(written[".scratch/hits.json"]),
        entries=2,
    )
    # The result itself never reaches the model, only the note that it exists.
    assert "a.md" not in (out.formatted or "")


async def test_txt_stores_the_text_the_model_would_have_seen(
    tool: GlobDocumentsTool, written: dict[str, str]
) -> None:
    plain = await tool("*.md")
    redirected = await tool("*.md", output_path=".scratch/hits.txt")

    assert written[".scratch/hits.txt"] == plain.formatted
    assert isinstance(redirected.data, RedirectedOutput)
    assert redirected.data.format == "txt"


async def test_another_suffix_is_refused(tool: GlobDocumentsTool) -> None:
    with pytest.raises(ToolRetry, match="must end in"):
        await tool("*.md", output_path=".scratch/hits.md")


async def test_a_tool_without_a_writer_refuses(tmp_path: Path) -> None:
    with pytest.raises(ToolRetry, match="not available"):
        await GlobDocumentsTool(paths=tmp_path)("*", output_path="~/o.json")


async def test_only_a_scratch_write_stays_off_the_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notified: list[str] = []

    class _Manager:
        def notify_scope_changed(
            self, owner: str, scope: str, *, exclude_client: str | None = None
        ) -> None:
            _ = exclude_client
            notified.append(f"{owner}:{scope}")

    monkeypatch.setattr(workspace_events, "manager", _Manager())

    async def mutator(path: str) -> str:
        return path

    mutate = announcing_mutator(mutator, "u")
    await mutate("~/.scratch/state.json")
    assert notified == []

    await mutate("~/report.md")
    assert notified == ["u:~"]


async def test_the_mcp_surface_leaves_the_argument_out() -> None:
    """MCP builds these tools with no writer, so it never advertises the redirect."""
    import hivegent.mcp.tools  # noqa: F401  registers the tools on the app
    from hivegent.mcp.app import mcp_app

    for name in ("grep", "read_document", "search"):
        tool = await mcp_app.get_tool(name)
        assert tool is not None
        assert "output_path" not in tool.parameters["properties"]
