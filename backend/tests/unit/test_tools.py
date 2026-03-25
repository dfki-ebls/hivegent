"""Unit tests for shared tool classes and ToolFactory."""

import json
from datetime import UTC, datetime
from pathlib import Path

from hivegent.chunks import (
    ChunkData,
    ChunkSummary,
    DocumentMetadata,
    GetChunkTool,
    ListChunksTool,
)
from hivegent.tools.documents import (
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    ListDocumentsTool,
)
from hivegent.tools.jq import JqTool
from hivegent.tools.mutations import EditDocumentTool, WriteDocumentTool


class TestListDocumentsTool:
    """Tests for ListDocumentsTool."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(path=tmp_path, glob="*.md")
        assert tool() == []

    def test_lists_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")  # not .md, should be ignored
        tool = ListDocumentsTool(path=tmp_path, glob="*.md")
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.md" in filenames
        assert "b.txt" not in filenames

    def test_custom_glob(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(path=tmp_path, glob="*.txt")
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.txt" in filenames
        assert "b.md" not in filenames

    def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(path=tmp_path, glob="*.md")
        result = tool(subdir="notes")
        filenames = [r.filename for r in result]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    def test_none_glob_lists_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        (tmp_path / "c.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(path=tmp_path)
        result = tool()
        filenames = {r.filename for r in result}
        assert filenames == {"a.md", "b.txt", "c.png"}

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(path=tmp_path / "nonexistent", glob="*.md")
        assert tool() == []


class TestGetDocumentTool:
    """Tests for GetDocumentTool."""

    def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("content here")
        tool = GetDocumentTool(path=tmp_path)
        assert tool("doc.md") == "content here"

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(path=tmp_path)
        assert tool("missing.md") is None

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(path=tmp_path)
        assert tool("../../../etc/passwd") is None


class TestGetDocumentLinesTool:
    """Tests for GetDocumentLinesTool."""

    def test_correct_range(self, tmp_path: Path) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5"]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = GetDocumentLinesTool(path=tmp_path)
        result = tool("doc.md", start=2, end=4)
        assert result is not None
        assert result.start_line == 2
        assert result.end_line == 4
        assert result.total_lines == 5
        assert result.content == "line2\nline3\nline4"

    def test_defaults_to_full_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = GetDocumentLinesTool(path=tmp_path)
        result = tool("doc.md")
        assert result is not None
        assert result.start_line == 1
        assert result.end_line == 3

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = GetDocumentLinesTool(path=tmp_path)
        assert tool("missing.md") is None

    def test_clamps_start_to_one(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("only")
        tool = GetDocumentLinesTool(path=tmp_path)
        result = tool("doc.md", start=-5)
        assert result is not None
        assert result.start_line == 1


class TestGlobDocumentsTool:
    """Tests for GlobDocumentsTool."""

    def test_matches_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("a")
        (tmp_path / "readme.md").write_text("b")
        tool = GlobDocumentsTool(path=tmp_path, glob="*.md")
        result = tool("note*")
        assert result == ["notes.md"]

    def test_custom_glob(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("a")
        (tmp_path / "data.md").write_text("b")
        tool = GlobDocumentsTool(path=tmp_path, glob="*.txt")
        result = tool("*")
        assert result == ["data.txt"]

    def test_none_glob_matches_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        tool = GlobDocumentsTool(path=tmp_path)
        result = tool("*")
        assert set(result) == {"a.md", "b.txt"}


class TestListChunksTool:
    """Tests for ListChunksTool."""

    def test_returns_chunks(self, tmp_path: Path) -> None:
        chunks = [ChunkSummary(token_count=10, start_index=0, end_index=50)]
        created_at = datetime(2024, 1, 1, tzinfo=UTC)
        metadata_dir = tmp_path / "metadata"
        metadata_dir.mkdir()
        (metadata_dir / "doc.json").write_text(
            DocumentMetadata(
                pipeline="test",
                created_at=created_at,
                stem_path="doc",
                description_path="doc.md",
                chunks=[
                    ChunkData(
                        text="chunk content",
                        token_count=10,
                        start_index=0,
                        end_index=50,
                    )
                ],
            ).model_dump_json()
        )
        tool = ListChunksTool(metadata_dir=metadata_dir)
        assert tool("doc.md") == chunks

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        tool = ListChunksTool(metadata_dir=tmp_path)
        assert tool("missing.md") is None


class TestGetChunkTool:
    """Tests for GetChunkTool."""

    def test_returns_chunk_text(self, tmp_path: Path) -> None:
        created_at = datetime(2024, 1, 1, tzinfo=UTC)
        metadata_dir = tmp_path / "metadata"
        metadata_dir.mkdir()
        (metadata_dir / "doc.json").write_text(
            DocumentMetadata(
                pipeline="test",
                created_at=created_at,
                stem_path="doc",
                description_path="doc.md",
                chunks=[
                    ChunkData(
                        text="chunk content",
                        token_count=10,
                        start_index=0,
                        end_index=50,
                    )
                ],
            ).model_dump_json()
        )
        tool = GetChunkTool(metadata_dir=metadata_dir)
        assert tool("doc.md", 0) == "chunk content"

    def test_returns_none_for_invalid_index(self, tmp_path: Path) -> None:
        created_at = datetime(2024, 1, 1, tzinfo=UTC)
        metadata_dir = tmp_path / "metadata"
        metadata_dir.mkdir()
        (metadata_dir / "doc.json").write_text(
            DocumentMetadata(
                pipeline="test",
                created_at=created_at,
                stem_path="doc",
                description_path="doc.md",
                chunks=[
                    ChunkData(
                        text="chunk content",
                        token_count=10,
                        start_index=0,
                        end_index=50,
                    )
                ],
            ).model_dump_json()
        )
        tool = GetChunkTool(metadata_dir=metadata_dir)
        assert tool("doc.md", 99) is None


class TestEditDocumentTool:
    """Tests for EditDocumentTool."""

    async def test_replaces_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        tool = EditDocumentTool(path=tmp_path)
        result = await tool("doc.md", "hello", "goodbye")
        assert "Replaced 1 occurrence" in result
        assert (tmp_path / "doc.md").read_text() == "goodbye world"

    async def test_calls_on_write(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        written: list[str] = []

        async def _on_write(filename: str) -> None:
            written.append(filename)

        tool = EditDocumentTool(path=tmp_path, on_write=_on_write)
        await tool("doc.md", "hello", "goodbye")
        assert written == ["doc.md"]

    async def test_error_on_missing_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        tool = EditDocumentTool(path=tmp_path)
        result = await tool("doc.md", "missing", "new")
        assert "Error" in result

    async def test_error_on_duplicate_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello hello")
        tool = EditDocumentTool(path=tmp_path)
        result = await tool("doc.md", "hello", "goodbye")
        assert "Error" in result
        assert "2 times" in result


class TestWriteDocumentTool:
    """Tests for WriteDocumentTool."""

    async def test_replace_creates_file(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(path=tmp_path, glob="*.md")
        result = await tool("new.md", "content")
        assert "Wrote" in result
        assert (tmp_path / "new.md").read_text() == "content"

    async def test_append(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("start")
        tool = WriteDocumentTool(path=tmp_path, glob="*.md")
        result = await tool("doc.md", " end", mode="append")
        assert "Appended" in result
        assert (tmp_path / "doc.md").read_text() == "start end"

    async def test_prepend(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("end")
        tool = WriteDocumentTool(path=tmp_path, glob="*.md")
        result = await tool("doc.md", "start ", mode="prepend")
        assert "Prepended" in result
        assert (tmp_path / "doc.md").read_text() == "start end"

    async def test_rejects_non_matching_glob(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(path=tmp_path, glob="*.md")
        result = await tool("doc.txt", "content")
        assert "Error" in result

    async def test_none_glob_allows_any(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(path=tmp_path)
        result = await tool("data.txt", "content")
        assert "Wrote" in result
        assert (tmp_path / "data.txt").read_text() == "content"

    async def test_calls_on_write(self, tmp_path: Path) -> None:
        written: list[str] = []

        async def _on_write(filename: str) -> None:
            written.append(filename)

        tool = WriteDocumentTool(path=tmp_path, glob="*.md", on_write=_on_write)
        await tool("doc.md", "content")
        assert written == ["doc.md"]


class TestJqTool:
    """Tests for JqTool."""

    async def test_single_file_query(self, tmp_path: Path) -> None:
        data = {"title": "Hello", "count": 42}
        (tmp_path / "item.json").write_text(json.dumps(data))
        tool = JqTool(path=tmp_path)
        result = json.loads(await tool(".title", "item.json"))
        assert result == ["Hello"]

    async def test_invalid_jq_expression(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"x": 1}))
        tool = JqTool(path=tmp_path)
        result = await tool("invalid [[[", "item.json")
        assert result.startswith("Error:")

    async def test_nonexistent_filename(self, tmp_path: Path) -> None:
        tool = JqTool(path=tmp_path)
        result = await tool(".", "missing.json")
        assert result.startswith("Error:")

    async def test_path_traversal(self, tmp_path: Path) -> None:
        tool = JqTool(path=tmp_path)
        result = await tool(".", "../etc/passwd")
        assert result.startswith("Error:")
