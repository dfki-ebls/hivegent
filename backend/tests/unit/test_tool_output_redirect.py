"""Tests for redirecting a tool's result into a workspace file.

A tool built with a writer takes an ``output_path``: the suffix picks which of
the result's two channels is stored, and the model is handed a receipt instead
of the result unless it is short. The write itself goes through the ordinary document gateway, so
these tests stub only that, not the tools around it.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from hivegent import workspace_events
from hivegent.changes import Changeset, Write
from hivegent.tools.base import ToolRetry
from hivegent.tools.documents import GlobDocumentsTool
from hivegent.tools.mutations import WriteDocumentTool
from hivegent.tools.sink import OutputSink, RedirectedOutput
from hivegent.workspace_events import announce_paths


@pytest.fixture()
def written() -> dict[str, str]:
    """Collect what the write gateway was asked to persist."""
    return {}


@pytest.fixture()
def writer(tmp_path: Path, written: dict[str, str]) -> WriteDocumentTool:
    """A writer over *tmp_path* that records instead of touching the store."""

    async def commit(changeset: Changeset[str]) -> str:
        (write,) = changeset.operations
        assert isinstance(write, Write)
        written[write.target] = write.content

        return f"wrote {write.target}"

    return WriteDocumentTool(paths=tmp_path, commit=commit)


@pytest.fixture()
def tool(tmp_path: Path, writer: WriteDocumentTool) -> GlobDocumentsTool:
    (tmp_path / "a.md").write_text("one")
    (tmp_path / "b.md").write_text("two")
    return GlobDocumentsTool(paths=tmp_path, sink=OutputSink(writer, 2_000))


async def test_json_stores_the_structured_result(
    tool: GlobDocumentsTool, written: dict[str, str]
) -> None:
    out = await tool(["*.md"], output_path="hits.json")

    assert written["hits.json"] == '["a.md","b.md"]'
    assert out.data == RedirectedOutput(
        output_path="hits.json",
        format="json",
        characters=len(written["hits.json"]),
        entries=2,
    )
    # Too short to be worth withholding, so the receipt repeats it.
    assert "a.md" in out.text


async def test_a_long_result_is_withheld(
    tool: GlobDocumentsTool, writer: WriteDocumentTool
) -> None:
    terse = replace(tool, sink=OutputSink(writer, 3))
    out = await terse(["*.md"], output_path="hits.json")

    assert "a.md" not in out.text


async def test_txt_stores_the_text_the_model_would_have_seen(
    tool: GlobDocumentsTool, written: dict[str, str]
) -> None:
    plain = await tool(["*.md"])
    redirected = await tool(["*.md"], output_path="hits.txt")

    assert written["hits.txt"] == plain.formatted
    assert isinstance(redirected.data, RedirectedOutput)
    assert redirected.data.format == "txt"


async def test_another_suffix_is_refused(tool: GlobDocumentsTool) -> None:
    with pytest.raises(ToolRetry, match="must end in"):
        await tool(["*.md"], output_path="hits.md")


async def test_a_tool_without_a_writer_refuses(tmp_path: Path) -> None:
    with pytest.raises(ToolRetry, match="not available"):
        await GlobDocumentsTool(paths=tmp_path)(["*"], output_path="~/o.json")


async def test_a_change_announces_each_workspace_once(
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

    announce_paths("u", "~/report.md", "~/notes/a.md")
    assert notified == ["u:~"]


async def test_the_mcp_surface_leaves_the_argument_out() -> None:
    """MCP builds these tools with no writer, so it never advertises the redirect."""
    import hivegent.mcp.tools  # noqa: F401  registers the tools on the app
    from hivegent.mcp.app import mcp_app

    for name in ("grep", "read_document", "search"):
        tool = await mcp_app.get_tool(name)
        assert tool is not None
        assert "output_path" not in tool.parameters["properties"]
