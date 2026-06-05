"""Unit tests for shared tool classes and ToolFactory."""

import json
from pathlib import Path

from fastapi import HTTPException

from hivegent.tools.base import SearchPath
from hivegent.tools.documents import (
    DocumentRange,
    DocumentSummary,
    DocumentTreeNode,
    GlobDocumentsTool,
    ListDocumentsTool,
    ReadDocumentTool,
)
from hivegent.tools.grep import GrepLine, GrepMatch, GrepTool
from hivegent.tools.jq import JqTool
from hivegent.tools.mutations import EditDocumentTool, WriteDocumentTool


def _as_summaries(
    data: list[DocumentSummary] | DocumentTreeNode,
) -> list[DocumentSummary]:
    """Narrow a ListDocumentsTool result to a list of summaries."""
    assert isinstance(data, list) and all(isinstance(d, DocumentSummary) for d in data)
    return data


class TestListDocumentsTool:
    """Tests for ListDocumentsTool (flat list and tree modes)."""

    # --- Flat list mode tests (default) ---

    def test_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        assert tool().data == []

    def test_lists_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = _as_summaries(tool().data)
        filenames = [r.filename for r in data]
        assert "a.md" in filenames
        assert "b.txt" not in filenames

    def test_custom_glob(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.txt")
        data = _as_summaries(tool().data)
        filenames = [r.filename for r in data]
        assert "a.txt" in filenames
        assert "b.md" not in filenames

    def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = _as_summaries(tool(path="notes").data)
        filenames = [r.filename for r in data]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    def test_none_glob_lists_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        (tmp_path / "c.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries(tool().data)
        filenames = {r.filename for r in data}
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
        data = _as_summaries(tool().data)
        filenames = {r.filename for r in data}
        assert filenames == {"a.md", "@team/b.md"}

    def test_includes_directories(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries(tool(max_depth=None).data)
        dirs = [r for r in data if r.is_directory]
        assert any(r.filename == "notes" for r in dirs)

    def test_max_depth_default_excludes_nested(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries(tool().data)
        filenames = {r.filename for r in data}
        assert "top.md" in filenames
        assert "notes" in filenames
        assert "notes/n.md" not in filenames

    def test_max_results_limits_list(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = ListDocumentsTool(paths=tmp_path)
        data = tool(max_results=3).data
        assert isinstance(data, list)
        assert len(data) == 3

    def test_skips_build_dirs_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries(tool(max_depth=None).data)
        filenames = {r.filename for r in data}
        assert "src.py" in filenames
        assert "__pycache__" not in filenames
        assert "__pycache__/junk.pyc" not in filenames

    def test_include_ignored_exposes_build_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries(tool(max_depth=None, include_ignored=True).data)
        filenames = {r.filename for r in data}
        assert "__pycache__" in filenames

    # --- Tree mode tests (flatten=False) ---

    def test_tree_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        result = tool(flatten=False)
        assert isinstance(result.data, DocumentTreeNode)
        assert result.data.children == ()
        assert result.formatted == "(empty)"

    def test_tree_single_level(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = tool(flatten=False, max_depth=None).data
        assert isinstance(data, DocumentTreeNode)
        names = [c.name for c in data.children]
        assert names == ["a.md", "b.md"]
        assert all(not c.is_directory for c in data.children)

    def test_tree_nested_structure(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = tool(flatten=False, max_depth=None).data
        assert isinstance(data, DocumentTreeNode)
        dir_children = [c for c in data.children if c.is_directory]
        assert len(dir_children) == 1
        assert dir_children[0].name == "notes"
        assert dir_children[0].children[0].name == "n.md"

    def test_tree_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = tool(path="notes", flatten=False, max_depth=None).data
        assert isinstance(data, DocumentTreeNode)
        assert len(data.children) == 1
        assert data.children[0].name == "notes"
        assert data.children[0].children[0].name == "n.md"

    def test_tree_max_depth(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("deep")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path)
        data = tool(flatten=False, max_depth=1).data
        assert isinstance(data, DocumentTreeNode)
        all_names = {c.name for c in data.children}
        assert "top.md" in all_names
        assert "a" in all_names

    def test_tree_max_results(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = ListDocumentsTool(paths=tmp_path)
        data = tool(flatten=False, max_results=3).data
        assert isinstance(data, DocumentTreeNode)
        assert len(data.children) == 3

    def test_tree_multi_store(self, tmp_path: Path) -> None:
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
        data = tool(flatten=False).data
        assert isinstance(data, DocumentTreeNode)
        names = {c.name for c in data.children}
        assert names == {"a.md", "@team"}

    def test_tree_formatted_output(self, tmp_path: Path) -> None:
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path)
        formatted = tool(flatten=False, max_depth=None).formatted
        assert formatted is not None
        assert "├── " in formatted or "└── " in formatted

    def test_tree_summary_line(self, tmp_path: Path) -> None:
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path)
        formatted = tool(flatten=False, max_depth=None).formatted
        assert formatted is not None
        assert "1 directory" in formatted
        assert "2 files" in formatted


class TestGlobDocumentsTool:
    """Tests for GlobDocumentsTool (pattern-based file matching)."""

    def test_matches_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("a")
        (tmp_path / "readme.md").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path, glob="*.md")
        assert tool("note*").data == ["notes.md"]

    def test_custom_base_glob(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("a")
        (tmp_path / "data.md").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path, glob="*.txt")
        assert tool("*").data == ["data.txt"]

    def test_none_base_matches_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path)
        data = tool("*").data
        assert isinstance(data, list)
        assert set(data) == {"a.md", "b.txt"}

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
        data = tool("*.md").data
        assert isinstance(data, list)
        assert set(data) == {"a.md", "@team/b.md"}

    def test_max_results(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = GlobDocumentsTool(paths=tmp_path)
        data = tool("*.txt", max_results=3).data
        assert isinstance(data, list)
        assert len(data) == 3

    def test_subdir_scoping(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "a.md").write_text("a")
        (tmp_path / "top.md").write_text("top")
        tool = GlobDocumentsTool(paths=tmp_path)
        data = tool("*.md", path="notes").data
        assert data == ["notes/a.md"]

    def test_skips_build_dirs_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.py").write_text("x")
        tool = GlobDocumentsTool(paths=tmp_path)
        data = tool("**/*.py").data
        assert "src.py" in data
        assert "__pycache__/junk.py" not in data

    def test_include_ignored_exposes_build_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.py").write_text("x")
        tool = GlobDocumentsTool(paths=tmp_path)
        data = tool("**/*.py", include_ignored=True).data
        assert "__pycache__/junk.py" in data


class TestReadDocumentTool:
    """Tests for ReadDocumentTool (line-range reads with line numbers)."""

    def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("content here")
        tool = ReadDocumentTool(paths=tmp_path)
        result = tool("doc.md").data
        assert isinstance(result, DocumentRange)
        assert result.content == "content here"
        assert result.start_line == 1
        assert result.end_line == 1
        assert result.total_lines == 1

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        assert tool("missing.md").data is None

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        assert tool("../../../etc/passwd").data is None

    def test_reads_group_document(self, tmp_path: Path) -> None:
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "doc.md").write_text("group content")
        tool = ReadDocumentTool(
            paths=(
                SearchPath(path=tmp_path),
                SearchPath(path=group_dir, prefix="@team"),
            )
        )
        result = tool("@team/doc.md").data
        assert isinstance(result, DocumentRange)
        assert result.content == "group content"

    def test_returns_none_for_unknown_prefix(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        assert tool("@unknown/doc.md").data is None

    def test_formatted_always_includes_line_numbers(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("alpha\nbeta")
        tool = ReadDocumentTool(paths=tmp_path)
        formatted = tool("doc.md").formatted
        assert formatted is not None
        assert "1: alpha" in formatted
        assert "2: beta" in formatted

    def test_char_cap_truncates_within_window(self, tmp_path: Path) -> None:
        content = "x" * 200
        (tmp_path / "big.md").write_text(content)
        tool = ReadDocumentTool(paths=tmp_path, max_chars=50)
        result = tool("big.md").data
        assert isinstance(result, DocumentRange)
        # Single 200-char line fits in the window so it's kept whole;
        # but a longer file with multiple lines would get clipped.
        assert len(result.content) == 200

    def test_char_cap_clips_multiline(self, tmp_path: Path) -> None:
        lines = ["y" * 50 for _ in range(10)]
        (tmp_path / "big.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path, max_chars=120)
        result = tool("big.md").data
        assert isinstance(result, DocumentRange)
        # 120-char budget fits ~2 lines (each 50 + newline = 51 chars).
        assert result.end_line < result.total_lines

    # --- offset / limit tests ---

    def test_correct_range(self, tmp_path: Path) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5"]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path)
        result = tool("doc.md", offset=2, limit=3).data
        assert isinstance(result, DocumentRange)
        assert result.start_line == 2
        assert result.end_line == 4
        assert result.total_lines == 5
        assert result.content == "line2\nline3\nline4"

    def test_defaults_to_full_file_when_small(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = ReadDocumentTool(paths=tmp_path)
        result = tool("doc.md").data
        assert isinstance(result, DocumentRange)
        assert result.start_line == 1
        assert result.end_line == 3
        assert result.content == "a\nb\nc"

    def test_offset_without_limit(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = ReadDocumentTool(paths=tmp_path)
        result = tool("doc.md", offset=2).data
        assert isinstance(result, DocumentRange)
        assert result.start_line == 2
        assert result.end_line == 3
        assert result.content == "b\nc"

    def test_default_window_caps_lines(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(5000)]
        (tmp_path / "big.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path)
        result = tool("big.md").data
        assert isinstance(result, DocumentRange)
        assert result.start_line == 1
        assert result.end_line == 2000
        assert result.total_lines == 5000

    def test_custom_default_lines(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(100)]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path, default_lines=10)
        result = tool("doc.md").data
        assert isinstance(result, DocumentRange)
        assert result.end_line == 10

    def test_continuation_hint_when_truncated(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(100)]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path, default_lines=10)
        formatted = tool("doc.md").formatted
        assert formatted is not None
        assert "more lines" in formatted
        assert "offset=11" in formatted


class TestEditDocumentTool:
    """The tool resolves and access-checks the path, then delegates to its mutator.

    The edit algorithm itself lives in ``workspace.edit_document_text`` and is
    covered by ``TestEditDocumentText``.
    """

    async def test_delegates_to_mutator(self, tmp_path: Path) -> None:
        calls: list[tuple[str, str, str, bool]] = []

        async def _mutate(
            filename: str,
            old_string: str,
            new_string: str,
            replace_all: bool,
        ) -> str:
            calls.append((filename, old_string, new_string, replace_all))
            return "edited"

        tool = EditDocumentTool(paths=tmp_path, mutator=_mutate)
        result = (await tool("doc.md", "hello", "goodbye", replace_all=True)).data
        assert result == "edited"
        assert calls == [("doc.md", "hello", "goodbye", True)]

    async def test_rejects_inaccessible_path(self, tmp_path: Path) -> None:
        tool = EditDocumentTool(paths=tmp_path, mutator=_unreachable_edit)
        result = (await tool("../escape.md", "a", "b")).data
        assert "not accessible" in result

    async def test_translates_mutator_error(self, tmp_path: Path) -> None:
        async def _mutate(*_: object) -> str:
            raise HTTPException(status_code=404, detail="Document not found")

        tool = EditDocumentTool(paths=tmp_path, mutator=_mutate)
        result = (await tool("doc.md", "a", "b")).data
        assert result == "Error: Document not found"


class TestWriteDocumentTool:
    """The tool resolves, access-checks, and glob-filters the path, then delegates.

    The write algorithm itself lives in ``workspace.write_document_text`` and is
    covered by ``TestWriteDocumentText``.
    """

    async def test_delegates_to_mutator(self, tmp_path: Path) -> None:
        calls: list[tuple[str, str, str]] = []

        async def _mutate(filename: str, content: str, mode: str) -> str:
            calls.append((filename, content, mode))
            return "written"

        tool = WriteDocumentTool(paths=tmp_path, glob="*.md", mutator=_mutate)
        result = (await tool("doc.md", "content", mode="append")).data
        assert result == "written"
        assert calls == [("doc.md", "content", "append")]

    async def test_rejects_non_matching_glob(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(
            paths=tmp_path, glob="*.md", mutator=_unreachable_write
        )
        result = (await tool("doc.txt", "content")).data
        assert "Error" in result

    async def test_none_glob_allows_any(self, tmp_path: Path) -> None:
        async def _mutate(filename: str, content: str, mode: str) -> str:
            return f"wrote {filename}"

        tool = WriteDocumentTool(paths=tmp_path, mutator=_mutate)
        result = (await tool("data.txt", "content")).data
        assert result == "wrote data.txt"

    async def test_translates_mutator_error(self, tmp_path: Path) -> None:
        async def _mutate(*_: object) -> str:
            raise HTTPException(status_code=400, detail="Unsupported write mode: x")

        tool = WriteDocumentTool(paths=tmp_path, mutator=_mutate)
        result = (await tool("doc.md", "content")).data
        assert result == "Error: Unsupported write mode: x"


async def _unreachable_edit(*_: object) -> str:
    raise AssertionError("mutator must not run when the path is rejected")


async def _unreachable_write(*_: object) -> str:
    raise AssertionError("mutator must not run when the path is rejected")


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

    async def test_nonexistent_file_path(self, tmp_path: Path) -> None:
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


class TestGrepFormatting:
    """Tests for GrepTool match formatting and the line-level char budget."""

    @staticmethod
    def _block(n: int) -> GrepMatch:
        return GrepMatch(
            filename="f.md",
            lines=tuple(GrepLine(line_number=i, text="x", is_match=True) for i in range(1, n + 1)),
        )

    def test_oversized_block_truncates_instead_of_dropping(self, tmp_path: Path) -> None:
        # A single merged block larger than the budget must still show its
        # leading lines (truncated), never just a notice with a stray "--".
        tool = GrepTool(paths=tmp_path, max_formatted_chars=40)
        formatted = tool._format_matches([self._block(50)])
        shown = formatted.count("f.md:")
        assert 0 < shown < 50
        assert formatted.startswith("f.md:1:")
        assert not formatted.startswith("\n---\n")
        assert f"({50 - shown} of 50 lines omitted" in formatted

    def test_fully_shown_has_no_omitted_notice(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted = tool._format_matches([self._block(3)])
        assert formatted.count("f.md:") == 3
        assert "omitted" not in formatted

    def test_separate_blocks_joined_by_separator(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted = tool._format_matches([self._block(1), self._block(1)])
        assert "\n---\n" in formatted
        assert "omitted" not in formatted
