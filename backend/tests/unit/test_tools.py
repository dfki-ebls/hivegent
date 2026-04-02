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
from hivegent.tools.base import SearchPath
from hivegent.tools.documents import (
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    ListDocumentsTool,
    TreeDocumentsTool,
)
from hivegent.tools.jq import JqTool
from hivegent.tools.mutations import EditDocumentTool, WriteDocumentTool


class TestListDocumentsTool:
    """Tests for ListDocumentsTool."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        assert tool().data == []

    def test_lists_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")  # not .md, should be ignored
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool().data
        filenames = [r.filename for r in result]
        assert "a.md" in filenames
        assert "b.txt" not in filenames

    def test_custom_glob(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.txt")
        result = tool().data
        filenames = [r.filename for r in result]
        assert "a.txt" in filenames
        assert "b.md" not in filenames

    def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool(subdir="notes").data
        filenames = [r.filename for r in result]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    def test_none_glob_lists_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        (tmp_path / "c.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(paths=tmp_path)
        result = tool().data
        filenames = {r.filename for r in result}
        assert filenames == {"a.md", "b.txt", "c.png"}

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path / "nonexistent", glob="*.md")
        assert tool().data == []

    def test_multi_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = ListDocumentsTool(
            paths=(
                SearchPath(path=user_dir),
                SearchPath(path=group_dir, prefix="@team"),
            )
        )
        filenames = {r.filename for r in tool().data}
        assert filenames == {"a.md", "@team/b.md"}

    def test_includes_directories(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        tool = ListDocumentsTool(paths=tmp_path)
        result = tool(max_depth=None).data
        dirs = [r for r in result if r.is_directory]
        assert any(r.filename == "notes" for r in dirs)

    def test_max_depth_default_excludes_nested(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path)
        filenames = {r.filename for r in tool().data}
        assert "top.md" in filenames
        assert "notes" in filenames
        assert "notes/n.md" not in filenames

    def test_max_results_limits_list(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = ListDocumentsTool(paths=tmp_path)
        result = tool(max_results=3).data
        assert len(result) == 3


class TestTreeDocumentsTool:
    """Tests for TreeDocumentsTool."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        tool = TreeDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool()
        assert result.data.children == ()
        assert result.formatted == "(empty)"

    def test_single_level(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = TreeDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool()
        names = [c.name for c in result.data.children]
        assert names == ["a.md", "b.md"]
        assert all(not c.is_directory for c in result.data.children)

    def test_nested_structure(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = TreeDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool()
        dir_children = [c for c in result.data.children if c.is_directory]
        assert len(dir_children) == 1
        assert dir_children[0].name == "notes"
        assert dir_children[0].children[0].name == "n.md"

    def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = TreeDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool(subdir="notes")
        # Only notes/n.md matches; tree has notes/ → n.md
        assert len(result.data.children) == 1
        assert result.data.children[0].name == "notes"
        assert result.data.children[0].children[0].name == "n.md"

    def test_max_depth(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("deep")
        (tmp_path / "top.md").write_text("top")
        tool = TreeDocumentsTool(paths=tmp_path)
        result = tool(max_depth=1)
        all_names = {c.name for c in result.data.children}
        assert "top.md" in all_names
        assert "a" in all_names

    def test_max_results(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = TreeDocumentsTool(paths=tmp_path)
        result = tool(max_results=3)
        assert len(result.data.children) == 3

    def test_multi_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = TreeDocumentsTool(
            paths=(
                SearchPath(path=user_dir),
                SearchPath(path=group_dir, prefix="@team"),
            )
        )
        names = {c.name for c in tool().data.children}
        assert names == {"a.md", "@team"}

    def test_formatted_output(self, tmp_path: Path) -> None:
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = TreeDocumentsTool(paths=tmp_path)
        formatted = tool().formatted
        assert formatted is not None
        assert "├── " in formatted or "└── " in formatted

    def test_summary_line(self, tmp_path: Path) -> None:
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = TreeDocumentsTool(paths=tmp_path)
        formatted = tool().formatted
        assert formatted is not None
        assert "1 directory" in formatted
        assert "2 files" in formatted


class TestGetDocumentTool:
    """Tests for GetDocumentTool."""

    def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("content here")
        tool = GetDocumentTool(paths=tmp_path)
        assert tool("doc.md").data == "content here"

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(paths=tmp_path)
        assert tool("missing.md").data is None

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(paths=tmp_path)
        assert tool("../../../etc/passwd").data is None

    def test_reads_group_document(self, tmp_path: Path) -> None:
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "doc.md").write_text("group content")
        tool = GetDocumentTool(
            paths=(
                SearchPath(path=tmp_path),
                SearchPath(path=group_dir, prefix="@team"),
            )
        )
        assert tool("@team/doc.md").data == "group content"

    def test_returns_none_for_unknown_prefix(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(paths=tmp_path)
        assert tool("@unknown/doc.md").data is None

    def test_truncates_large_file(self, tmp_path: Path) -> None:
        content = "x" * 200
        (tmp_path / "big.md").write_text(content)
        tool = GetDocumentTool(paths=tmp_path)
        result = tool("big.md", max_chars=50).data
        assert result is not None
        assert result.startswith("x" * 50)
        assert "[truncated" in result
        assert len(result.split("\n\n[truncated")[0]) == 50


class TestGetDocumentLinesTool:
    """Tests for GetDocumentLinesTool."""

    def test_correct_range(self, tmp_path: Path) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5"]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = GetDocumentLinesTool(paths=tmp_path)
        result = tool("doc.md", start=2, end=4).data
        assert result is not None
        assert result.start_line == 2
        assert result.end_line == 4
        assert result.total_lines == 5
        assert result.content == "line2\nline3\nline4"

    def test_defaults_to_full_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = GetDocumentLinesTool(paths=tmp_path)
        result = tool("doc.md").data
        assert result is not None
        assert result.start_line == 1
        assert result.end_line == 3

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = GetDocumentLinesTool(paths=tmp_path)
        assert tool("missing.md").data is None

    def test_clamps_start_to_one(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("only")
        tool = GetDocumentLinesTool(paths=tmp_path)
        result = tool("doc.md", start=-5).data
        assert result is not None
        assert result.start_line == 1

    def test_default_end_caps_lines(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(500)]
        (tmp_path / "big.md").write_text("\n".join(lines))
        tool = GetDocumentLinesTool(paths=tmp_path)
        result = tool("big.md").data
        assert result is not None
        assert result.start_line == 1
        assert result.end_line == 200
        assert result.total_lines == 500

    def test_custom_default_lines(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(100)]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = GetDocumentLinesTool(paths=tmp_path, default_lines=10)
        result = tool("doc.md").data
        assert result is not None
        assert result.end_line == 10


class TestGlobDocumentsTool:
    """Tests for GlobDocumentsTool."""

    def test_matches_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("a")
        (tmp_path / "readme.md").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path, glob="*.md")
        assert tool("note*").data == ["notes.md"]

    def test_custom_glob(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("a")
        (tmp_path / "data.md").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path, glob="*.txt")
        assert tool("*").data == ["data.txt"]

    def test_none_glob_matches_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path)
        assert set(tool("*").data) == {"a.md", "b.txt"}

    def test_multi_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = GlobDocumentsTool(
            paths=(
                SearchPath(path=user_dir),
                SearchPath(path=group_dir, prefix="@team"),
            )
        )
        assert set(tool("*.md").data) == {"a.md", "@team/b.md"}

    def test_max_results_limits_glob(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = GlobDocumentsTool(paths=tmp_path)
        assert len(tool("*.txt", max_results=3).data) == 3


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
        tool = ListChunksTool(paths=metadata_dir)
        assert tool("doc.md").data == chunks

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        tool = ListChunksTool(paths=tmp_path)
        assert tool("missing.md").data is None


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
        tool = GetChunkTool(paths=metadata_dir)
        assert tool("doc.md", 0).data == "chunk content"

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
        tool = GetChunkTool(paths=metadata_dir)
        assert tool("doc.md", 99).data is None


class TestEditDocumentTool:
    """Tests for EditDocumentTool."""

    async def test_replaces_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        tool = EditDocumentTool(paths=tmp_path)
        result = (await tool("doc.md", "hello", "goodbye")).data
        assert "Replaced 1 occurrence" in result
        assert (tmp_path / "doc.md").read_text() == "goodbye world"

    async def test_calls_on_write(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        written: list[str] = []

        async def _on_write(filename: str) -> None:
            written.append(filename)

        tool = EditDocumentTool(paths=tmp_path, hook=_on_write)
        await tool("doc.md", "hello", "goodbye")
        assert written == ["doc.md"]

    async def test_error_on_missing_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        tool = EditDocumentTool(paths=tmp_path)
        result = (await tool("doc.md", "missing", "new")).data
        assert "Error" in result

    async def test_error_on_duplicate_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello hello")
        tool = EditDocumentTool(paths=tmp_path)
        result = (await tool("doc.md", "hello", "goodbye")).data
        assert "Error" in result
        assert "2 times" in result


class TestWriteDocumentTool:
    """Tests for WriteDocumentTool."""

    async def test_replace_creates_file(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path, glob="*.md")
        result = (await tool("new.md", "content")).data
        assert "Wrote" in result
        assert (tmp_path / "new.md").read_text() == "content"

    async def test_append(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("start")
        tool = WriteDocumentTool(paths=tmp_path, glob="*.md")
        result = (await tool("doc.md", " end", mode="append")).data
        assert "Appended" in result
        assert (tmp_path / "doc.md").read_text() == "start end"

    async def test_prepend(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("end")
        tool = WriteDocumentTool(paths=tmp_path, glob="*.md")
        result = (await tool("doc.md", "start ", mode="prepend")).data
        assert "Prepended" in result
        assert (tmp_path / "doc.md").read_text() == "start end"

    async def test_rejects_non_matching_glob(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path, glob="*.md")
        result = (await tool("doc.txt", "content")).data
        assert "Error" in result

    async def test_none_glob_allows_any(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path)
        result = (await tool("data.txt", "content")).data
        assert "Wrote" in result
        assert (tmp_path / "data.txt").read_text() == "content"

    async def test_calls_on_write(self, tmp_path: Path) -> None:
        written: list[str] = []

        async def _on_write(filename: str) -> None:
            written.append(filename)

        tool = WriteDocumentTool(paths=tmp_path, glob="*.md", hook=_on_write)
        await tool("doc.md", "content")
        assert written == ["doc.md"]


class TestJqTool:
    """Tests for JqTool."""

    async def test_single_file_query(self, tmp_path: Path) -> None:
        data = {"title": "Hello", "count": 42}
        (tmp_path / "item.json").write_text(json.dumps(data))
        tool = JqTool(paths=tmp_path)
        result = json.loads((await tool(".title", "item.json")).data)
        assert result == ["Hello"]

    async def test_invalid_jq_expression(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"x": 1}))
        tool = JqTool(paths=tmp_path)
        result = (await tool("invalid [[[", "item.json")).data
        assert result.startswith("Error:")

    async def test_nonexistent_filename(self, tmp_path: Path) -> None:
        tool = JqTool(paths=tmp_path)
        result = (await tool(".", "missing.json")).data
        assert result.startswith("Error:")

    async def test_path_traversal(self, tmp_path: Path) -> None:
        tool = JqTool(paths=tmp_path)
        result = (await tool(".", "../etc/passwd")).data
        assert result.startswith("Error:")

    async def test_large_output_truncated(self, tmp_path: Path) -> None:
        data = {"items": ["x" * 100 for _ in range(50)]}
        (tmp_path / "big.json").write_text(json.dumps(data))
        tool = JqTool(paths=tmp_path, max_output_chars=100)
        result = (await tool(".", "big.json")).data
        assert "[truncated]" in result
        assert len(result.split("\n\n[truncated]")[0]) == 100
