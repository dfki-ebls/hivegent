"""Unit tests for shared tool classes and ToolFactory."""

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic_monty import AsyncMonty

from hivegent.config import content_hash
from hivegent.converters import VISION_MEDIA_TYPES
from hivegent.store import WorkspaceScope
from hivegent.tools import python as python_tool
from hivegent.tools.base import (
    SearchPath,
    ToolRetry,
    resolve_accessible_file,
    scope_paths,
)
from hivegent.tools.binary import ReadBinaryDocumentTool
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
from hivegent.tools.python import PythonResult, RunPythonTool
from hivegent.types import DocumentFilter


def _as_summaries(
    data: list[DocumentSummary] | DocumentTreeNode,
) -> list[DocumentSummary]:
    """Narrow a ListDocumentsTool result to a list of summaries."""
    assert isinstance(data, list) and all(isinstance(d, DocumentSummary) for d in data)
    return data


class TestScopePaths:
    """Tests for the scope_paths helper that narrows by workspace prefix."""

    PATHS = (
        SearchPath(path=Path("/u"), scope=WorkspaceScope()),
        SearchPath(path=Path("/g"), scope=WorkspaceScope("team")),
    )

    def test_prefix_scopes_to_one_workspace(self) -> None:
        assert scope_paths(self.PATHS, "~/reports") == ((self.PATHS[0],), "reports")
        assert scope_paths(self.PATHS, "@team/reports") == ((self.PATHS[1],), "reports")

    def test_bare_prefix_selects_workspace_root(self) -> None:
        assert scope_paths(self.PATHS, "~") == ((self.PATHS[0],), None)
        assert scope_paths(self.PATHS, "@team") == ((self.PATHS[1],), None)

    def test_unprefixed_value_spans_every_workspace(self) -> None:
        assert scope_paths(self.PATHS, "reports") == (self.PATHS, "reports")
        assert scope_paths(self.PATHS, None) == (self.PATHS, None)

    def test_unknown_prefix_falls_through(self) -> None:
        assert scope_paths(self.PATHS, "@ghost/x") == (self.PATHS, "@ghost/x")


class TestPathCanonicalization:
    """A filter must see the file an operation touches, not the alias it named."""

    @staticmethod
    def _scoped(root: Path) -> SearchPath:
        """A personal-workspace root that hides ``excluded.md``."""
        return SearchPath(
            path=root,
            scope=WorkspaceScope(),
            filter_func=DocumentFilter(excluded=frozenset({"excluded.md"})),
        )

    def test_traversal_alias_cannot_reach_a_filtered_document(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "excluded.md").write_text("secret")
        (tmp_path / "sub").mkdir()
        paths = (self._scoped(tmp_path),)

        assert resolve_accessible_file(paths, "~/excluded.md") is None
        assert resolve_accessible_file(paths, "~/sub/../excluded.md") is None

    def test_symlink_alias_cannot_reach_a_filtered_document(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "excluded.md").write_text("secret")
        (tmp_path / "alias.md").symlink_to(tmp_path / "excluded.md")

        assert resolve_accessible_file((self._scoped(tmp_path),), "~/alias.md") is None

    def test_resolved_path_is_returned_canonically(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "allowed.md").write_text("ok")

        resolved = resolve_accessible_file(
            (self._scoped(tmp_path),), "~/sub/../allowed.md"
        )

        assert resolved is not None
        assert resolved[1] == "allowed.md"

    def test_listing_subdirectory_cannot_escape_its_filter(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "excluded.md").write_text("secret")
        (tmp_path / "sub").mkdir()
        tool = GlobDocumentsTool(paths=(self._scoped(tmp_path),))

        assert tool("*.md", path="~/sub/..").data == []


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

    def test_assets_contents_hidden_unless_ignored_included(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "doc.md").write_text("text")
        assets = tmp_path / "doc.assets"
        assets.mkdir()
        (assets / "img.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(paths=tmp_path)
        filenames = {r.filename for r in _as_summaries(tool(max_depth=None).data)}
        assert filenames == {"doc.md", "doc.assets"}
        revealed = {
            r.filename
            for r in _as_summaries(tool(max_depth=None, include_ignored=True).data)
        }
        assert "doc.assets/img.png" in revealed

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
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
            )
        )
        data = _as_summaries(tool().data)
        filenames = {r.filename for r in data}
        assert filenames == {"a.md", "@team/b.md"}

    def test_prefix_scopes_to_one_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = ListDocumentsTool(
            paths=(
                SearchPath(path=user_dir, scope=WorkspaceScope()),
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
            )
        )
        filenames = {r.filename for r in _as_summaries(tool(path="@team").data)}
        assert filenames == {"@team/b.md"}

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

    def test_empty_result_hint_counts_hidden_entries(self, tmp_path: Path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "a.pyc").write_bytes(b"x")
        (cache / "b.pyc").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)
        result = tool(max_depth=None)
        assert result.data == []
        assert result.formatted is not None
        assert "3 hidden entries" in result.formatted
        assert "include_ignored=True" in result.formatted
        assert tool(max_depth=None, include_ignored=True).formatted != result.formatted

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
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
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
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
            )
        )
        data = tool("*.md").data
        assert isinstance(data, list)
        assert set(data) == {"a.md", "@team/b.md"}

    def test_prefix_scopes_to_one_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = GlobDocumentsTool(
            paths=(
                SearchPath(path=user_dir, scope=WorkspaceScope()),
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
            )
        )
        assert tool("*.md", path="~").data == ["~/a.md"]

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
        assert result.content_hash  # surfaced for optimistic-concurrency edits

    def test_reads_file_named_with_a_decomposed_path(self, tmp_path: Path) -> None:
        # The production failure: the file is stored precomposed but a model can
        # only emit the decomposed spelling of a path it was shown, and on a
        # normalization-sensitive filesystem the two name different files.
        # Escapes, not literals: this file is saved precomposed, so writing both
        # spellings out would compare NFC with NFC and assert nothing.
        (tmp_path / "S\u00dcVOA.md").write_text("content here")
        tool = ReadDocumentTool(paths=tmp_path)
        result = tool("SU\u0308VOA.md").data
        assert isinstance(result, DocumentRange)
        assert result.content == "content here"

    def test_binary_original_directs_to_markdown_sidecar(self, tmp_path: Path) -> None:
        # A non-decodable original (report.docx) is never silently swapped; the
        # retry points the model at its <stem>.md sidecar so the read re-runs the
        # normal path resolution instead of following a sibling behind the scenes.
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        (tmp_path / "report.md").write_text("extracted text")
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="report.md"):
            tool("report.docx")

    @pytest.mark.parametrize("suffix", sorted(VISION_MEDIA_TYPES))
    def test_supported_binary_directs_to_binary_tool(
        self, tmp_path: Path, suffix: str
    ) -> None:
        # A vision-capable binary is sent to read_binary_document, on its name
        # alone. The write gateway refuses the same set from the same table, so
        # the two tools cannot drift into disagreeing about what counts as text
        # (see test_workspace_mutations for the other half).
        (tmp_path / f"scan{suffix}").write_text("text wearing a binary extension")
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="read_binary_document"):
            tool(f"scan{suffix}")

    async def test_binary_tool_points_unshowable_input_at_its_sidecar(
        self, tmp_path: Path
    ) -> None:
        # An Office document is neither showable nor readable as text, so the
        # refusal names the extracted text rather than the tool that would
        # refuse it in turn.
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        tool = ReadBinaryDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="report.md"):
            await tool("report.docx")

    async def test_binary_tool_preserves_scope_in_sidecar_hint(
        self, tmp_path: Path
    ) -> None:
        group_dir = tmp_path / "team"
        group_dir.mkdir()
        (group_dir / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        tool = ReadBinaryDocumentTool(
            paths=SearchPath(path=group_dir, scope=WorkspaceScope("team"))
        )

        with pytest.raises(ToolRetry, match=r"@team/report\.md"):
            await tool("@team/report.docx")

    def test_binary_without_companion_retries(self, tmp_path: Path) -> None:
        # Undecodable bytes become a recoverable ToolRetry, never a run-aborting
        # UnicodeDecodeError.
        (tmp_path / "blob.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xec\xec\xff\xfe")
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not text"):
            tool("blob.bin")

    def test_legacy_encoding_is_decoded_and_reported(self, tmp_path: Path) -> None:
        # A cp1252/UTF-16 original is content, not a binary: it is decoded
        # rather than refused, and the source encoding is named so a wrong
        # guess on short input is visible instead of silent.
        (tmp_path / "settings.ini").write_bytes("Benutzer = Jörg\n".encode("utf-16"))
        tool = ReadDocumentTool(paths=tmp_path)

        result = tool("settings.ini")

        assert isinstance(result.data, DocumentRange)
        assert result.data.content == "Benutzer = Jörg"
        assert result.formatted is not None
        assert "decoded from utf-16" in result.formatted

    def test_rejects_nonexistent(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            tool("missing.md")

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            tool("../../../etc/passwd")

    def test_reads_group_document(self, tmp_path: Path) -> None:
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "doc.md").write_text("group content")
        tool = ReadDocumentTool(
            paths=(
                SearchPath(path=tmp_path),
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
            )
        )
        result = tool("@team/doc.md").data
        assert isinstance(result, DocumentRange)
        assert result.content == "group content"

    def test_rejects_unknown_prefix(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            tool("@unknown/doc.md")

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

    def test_long_line_truncated_in_formatted_only(self, tmp_path: Path) -> None:
        # A base64-image line the window returns whole (it is the first
        # selected line) must not flood the model context: the formatted
        # output truncates it, while the structured content keeps it intact.
        long_line = "data:image/png;base64," + "A" * 500_000
        (tmp_path / "img.md").write_text(f"{long_line}\ntail")
        tool = ReadDocumentTool(paths=tmp_path, max_line_chars=80)
        out = tool("img.md")
        assert out.formatted is not None
        assert "…" in out.formatted
        assert len(max(out.formatted.splitlines(), key=len)) < 200
        assert isinstance(out.data, DocumentRange)
        assert long_line in out.data.content

    def test_tabular_file_is_pointed_at_query_table(self, tmp_path: Path) -> None:
        # The one moment the caller finds out a line read was the wrong tool
        # for this file is when it reads one, so the read says so.
        (tmp_path / "sales.csv").write_text("region,amount\nEU,100")
        tool = ReadDocumentTool(paths=tmp_path)
        formatted = tool("sales.csv").formatted

        assert formatted is not None
        assert "query_table" in formatted

    def test_full_lines_opts_out_of_the_per_line_clip(self, tmp_path: Path) -> None:
        # A wide markdown table row loses its trailing columns to the clip with
        # nothing but an ellipsis to show for it, which the reader cannot spot
        # from the output alone: the clip is named, and full_lines undoes it.
        row = "| " + " | ".join(f"col{i}" for i in range(200)) + " |"
        (tmp_path / "table.md").write_text(row)
        tool = ReadDocumentTool(paths=tmp_path, max_line_chars=80)

        clipped = tool("table.md").formatted
        assert clipped is not None
        assert "full_lines=true" in clipped
        assert "col199" not in clipped

        whole = tool("table.md", full_lines=True).formatted
        assert whole is not None
        assert "col199" in whole
        assert "full_lines=true" not in whole

    def test_formatted_budget_shrinks_range_and_continuation(
        self, tmp_path: Path
    ) -> None:
        # The rendered budget decides what the model actually saw, so both the
        # reported range and the follow-up offset track it; otherwise the next
        # call resumes past the lines that never fit.
        (tmp_path / "doc.md").write_text("\n".join(f"line{i}" for i in range(100)))
        tool = ReadDocumentTool(paths=tmp_path, max_formatted_chars=40)
        out = tool("doc.md")

        assert isinstance(out.data, DocumentRange)
        assert 0 < out.data.end_line < 100
        assert out.formatted is not None
        assert f"offset={out.data.end_line + 1}" in out.formatted
        assert out.data.content.splitlines()[-1] == f"line{out.data.end_line - 1}"

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
        calls: list[tuple[str, str, str, bool, str | None]] = []

        async def _mutate(
            filename: str,
            old_string: str,
            new_string: str,
            replace_all: bool,
            expected_hash: str | None,
        ) -> str:
            calls.append((filename, old_string, new_string, replace_all, expected_hash))
            return "edited"

        tool = EditDocumentTool(paths=tmp_path, mutator=_mutate)
        result = (
            await tool(
                "doc.md", "hello", "goodbye", replace_all=True, expected_hash="h"
            )
        ).data
        assert result == "edited"
        assert calls == [("doc.md", "hello", "goodbye", True, "h")]

    async def test_rejects_inaccessible_path(self, tmp_path: Path) -> None:
        tool = EditDocumentTool(paths=tmp_path, mutator=_unreachable_edit)
        with pytest.raises(ToolRetry, match="not accessible"):
            await tool("../escape.md", "a", "b")

    async def test_translates_mutator_error(self, tmp_path: Path) -> None:
        async def _mutate(*_: object) -> str:
            raise HTTPException(status_code=404, detail="Document not found")

        tool = EditDocumentTool(paths=tmp_path, mutator=_mutate)
        with pytest.raises(ToolRetry, match="Document not found"):
            await tool("doc.md", "a", "b")


class TestWriteDocumentTool:
    """The tool resolves, access-checks, and glob-filters the path, then delegates.

    The write algorithm itself lives in ``workspace.write_document_text`` and is
    covered by ``TestWriteDocumentText``.
    """

    async def test_delegates_to_mutator(self, tmp_path: Path) -> None:
        calls: list[tuple[str, str, str, str | None]] = []

        async def _mutate(
            filename: str, content: str, mode: str, expected_hash: str | None
        ) -> str:
            calls.append((filename, content, mode, expected_hash))
            return "written"

        tool = WriteDocumentTool(paths=tmp_path, glob="*.md", mutator=_mutate)
        result = (
            await tool("doc.md", "content", mode="append", expected_hash="h")
        ).data
        assert result == "written"
        assert calls == [("doc.md", "content", "append", "h")]

    async def test_rejects_non_matching_glob(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(
            paths=tmp_path, glob="*.md", mutator=_unreachable_write
        )
        with pytest.raises(ToolRetry, match="does not match pattern"):
            await tool("doc.txt", "content")

    async def test_none_glob_allows_any(self, tmp_path: Path) -> None:
        async def _mutate(
            filename: str, content: str, mode: str, expected_hash: str | None
        ) -> str:
            return f"wrote {filename}"

        tool = WriteDocumentTool(paths=tmp_path, mutator=_mutate)
        result = (await tool("data.txt", "content")).data
        assert result == "wrote data.txt"

    async def test_translates_mutator_error(self, tmp_path: Path) -> None:
        async def _mutate(*_: object) -> str:
            raise HTTPException(status_code=400, detail="Unsupported write mode: x")

        tool = WriteDocumentTool(paths=tmp_path, mutator=_mutate)
        with pytest.raises(ToolRetry, match="Unsupported write mode"):
            await tool("doc.md", "content")


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
        with pytest.raises(ToolRetry):
            await tool("invalid [[[", "item.json")

    async def test_nonexistent_file_path(self, tmp_path: Path) -> None:
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool(".", "missing.json")

    async def test_path_traversal(self, tmp_path: Path) -> None:
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool(".", "../etc/passwd")

    async def test_large_output_truncated(self, tmp_path: Path) -> None:
        data = {"items": ["x" * 100 for _ in range(50)]}
        (tmp_path / "big.json").write_text(json.dumps(data))
        tool = JqTool(paths=tmp_path, max_output_chars=100)
        result = (await tool(".", "big.json")).data

        # The budget is a budget, marker included, and both ends of the JSON
        # survive so the shape of the value is still readable.
        assert len(result) == 100
        assert "[truncated]" in result
        assert result.startswith('[{"items":') and result.endswith("]}]")


class TestGrepSearch:
    """Tests for GrepTool against real files on disk."""

    @staticmethod
    def _filenames(output: list[GrepMatch]) -> set[str]:
        return {m.filename for m in output}

    async def test_legacy_encoded_sibling_keeps_other_results(
        self, tmp_path: Path
    ) -> None:
        # A Latin-1 line reaches the JSON output base64-encoded rather than as
        # text; mis-parsing it used to discard every match of the whole root.
        (tmp_path / "legacy.xml").write_bytes(
            "<a name='Leitfähigkeit' unit='°C' />\n".encode("cp1252")
        )
        (tmp_path / "modern.xml").write_text("<a name='utf8' />\n")
        result = await GrepTool(paths=tmp_path)("name=")
        assert self._filenames(result.data) == {"legacy.xml", "modern.xml"}
        assert "Leitfähigkeit" in result.text

    async def test_original_dropped_when_description_matches(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "doc.xml").write_text("<a>needle</a>\n")
        (tmp_path / "doc.md").write_text("```xml\n<a>needle</a>\n```\n")
        (tmp_path / "raw.xml").write_text("<a>needle</a>\n")
        result = await GrepTool(paths=tmp_path)("needle")
        assert self._filenames(result.data) == {"doc.md", "raw.xml"}

    async def test_original_kept_when_globbed(self, tmp_path: Path) -> None:
        (tmp_path / "doc.xml").write_text("<a>needle</a>\n")
        (tmp_path / "doc.md").write_text("```xml\n<a>needle</a>\n```\n")
        result = await GrepTool(paths=tmp_path)("needle", glob="*.xml")
        assert self._filenames(result.data) == {"doc.xml"}

    async def test_assets_payload_hidden_unless_ignored(self, tmp_path: Path) -> None:
        assets = tmp_path / "doc.assets"
        assets.mkdir()
        (assets / "fig1.txt").write_text("needle\n")
        hidden = await GrepTool(paths=tmp_path)("needle")
        assert hidden.data == []
        revealed = await GrepTool(paths=tmp_path)("needle", include_ignored=True)
        assert self._filenames(revealed.data) == {"doc.assets/fig1.txt"}


class TestGrepFormatting:
    """Tests for GrepTool match formatting and the line-level char budget."""

    @staticmethod
    def _block(n: int, filename: str = "f.md") -> GrepMatch:
        return GrepMatch(
            filename=filename,
            lines=tuple(
                GrepLine(line_number=i, text="x", is_match=True)
                for i in range(1, n + 1)
            ),
        )

    def test_grouped_under_single_heading_with_line_prefixes(
        self, tmp_path: Path
    ) -> None:
        # The path appears once as a heading; lines carry only their number
        # with ``:`` for matches and ``-`` for context.
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        block = GrepMatch(
            filename="doc.md",
            lines=(
                GrepLine(line_number=10, text="ctx", is_match=False),
                GrepLine(line_number=11, text="hit", is_match=True),
            ),
        )
        formatted = tool._format_matches([block])
        assert formatted == "doc.md\n10-ctx\n11:hit"

    def test_oversized_block_truncates_instead_of_dropping(
        self, tmp_path: Path
    ) -> None:
        # A single merged block larger than the budget must still show its
        # leading lines (truncated), never just a heading with no content.
        tool = GrepTool(paths=tmp_path, max_formatted_chars=40)
        formatted = tool._format_matches([self._block(50)])
        shown = formatted.count(":x")
        assert 0 < shown < 50
        assert formatted.startswith("f.md\n1:x")
        assert not formatted.startswith("\n---\n")
        assert f"({50 - shown} of 50 lines omitted" in formatted

    def test_fully_shown_has_no_omitted_notice(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted = tool._format_matches([self._block(3)])
        assert formatted == "f.md\n1:x\n2:x\n3:x"
        assert "omitted" not in formatted

    def test_blocks_within_document_separated_by_dashes(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted = tool._format_matches([self._block(1), self._block(1)])
        assert formatted == "f.md\n1:x\n--\n1:x"

    def test_separate_documents_joined_by_separator(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted = tool._format_matches(
            [self._block(1, "a.md"), self._block(1, "b.md")]
        )
        assert formatted == "a.md\n1:x\n---\nb.md\n1:x"
        assert "omitted" not in formatted


def _recording_python_tool(
    tool: RunPythonTool, workspace: Path
) -> tuple[RunPythonTool, list[tuple[str, str, str, str | None]]]:
    """Attach a scoped writer and return its recorded mutations."""
    calls: list[tuple[str, str, str, str | None]] = []

    async def mutate(
        path: str, content: str, mode: str, expected_hash: str | None
    ) -> str:
        calls.append((path, content, mode, expected_hash))
        return "written"

    scoped = SearchPath(path=workspace, scope=WorkspaceScope())
    configured = replace(
        tool,
        paths=(scoped,),
        writer=WriteDocumentTool(paths=(scoped,), mutator=mutate),
    )
    return configured, calls


class TestRunPythonTool:
    """Tests for RunPythonTool."""

    @pytest.fixture()
    async def tool(self) -> AsyncIterator[RunPythonTool]:
        async with AsyncMonty(min_processes=1) as pool:
            yield RunPythonTool(pool=pool)

    async def test_returns_value_and_printed_output(self, tool: RunPythonTool) -> None:
        result = await tool("import math\nprint('working')\nmath.factorial(5)")
        assert result.data == PythonResult(result="120", stdout="working")
        assert result.text == "working\nResult: 120"

    async def test_statement_only_program_has_no_result(
        self, tool: RunPythonTool
    ) -> None:
        result = await tool("total = 1 + 1")
        assert result.data == PythonResult()
        assert "no value" in result.text

    async def test_failure_retries_with_traceback_and_prior_output(
        self, tool: RunPythonTool
    ) -> None:
        with pytest.raises(ToolRetry, match="ZeroDivisionError") as exc_info:
            await tool("print('before')\n1 / 0")
        assert "before" in str(exc_info.value)

    async def test_failure_diagnostic_fits_output_budget(
        self, tool: RunPythonTool
    ) -> None:
        capped = replace(tool, max_output_chars=80)

        with pytest.raises(ToolRetry) as exc_info:
            await capped("raise ValueError('x' * 10_000)")

        diagnostic = str(exc_info.value)
        assert len(diagnostic) <= 80
        assert "truncated" in diagnostic

    async def test_time_limit_bounds_a_runaway_program(
        self, tool: RunPythonTool
    ) -> None:
        bounded = replace(tool, limits={"max_duration_secs": 0.2})
        with pytest.raises(ToolRetry, match="TimeoutError"):
            await bounded("while True:\n    pass")

    async def test_printed_output_is_capped(self, tool: RunPythonTool) -> None:
        capped = replace(tool, max_output_chars=20)
        result = await capped("for i in range(50):\n    print('line', i)")
        assert result.data.truncated
        assert "more printed lines]" in result.text

    async def test_long_printed_line_fits_output_budget(
        self, tool: RunPythonTool
    ) -> None:
        result = await tool("print('x' * 10_000)")
        assert result.data.stdout == "x" * 10_000

        capped = replace(tool, max_output_chars=20)
        result = await capped("print('x' * 100)")
        assert result.data.stdout == "x" * 19 + "…"
        assert result.data.truncated

    async def test_temporary_files_are_private_to_one_run(
        self, tool: RunPythonTool
    ) -> None:
        result = await tool(
            "import os\n"
            "from pathlib import Path\n"
            'temp = Path(os.getenv("TMPDIR")) / "work.txt"\n'
            'temp.write_text("intermediate")\n'
            "temp.read_text()"
        )
        next_run = await tool(
            'from pathlib import Path\nPath("/tmp/work.txt").exists()'
        )

        assert result.data.result == "'intermediate'"
        assert next_run.data.result == "False"

    async def test_oversized_input_is_refused_before_it_is_decoded(
        self, tool: RunPythonTool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The budget bounds host memory, so a file that cannot fit it must never
        # reach the decoder: twenty large inputs would otherwise all be read in
        # before a single check ran.
        (tmp_path / "big.txt").write_text("x" * 1000)
        monkeypatch.setattr(
            python_tool,
            "read_text_or_retry",
            lambda *args, **kwargs: pytest.fail("the input was decoded"),
        )
        bounded = replace(
            tool,
            paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),),
            max_workspace_chars=10,
        )

        with pytest.raises(ToolRetry, match="~/big.txt"):
            await bounded("1", input_paths=("~/big.txt",))

    async def test_stored_script_reads_and_writes_scoped_files(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "script.py").write_text(
            'from pathlib import Path\n'
            'text = Path("/workspace/~/input.txt").read_text()\n'
            'output = Path("/workspace/~/output.txt")\n'
            'assert not output.exists()\n'
            'output.write_text(text.upper())\n'
            "len(text)"
        )
        (tmp_path / "input.txt").write_text("hello")
        calls: list[tuple[str, str, str, str | None]] = []

        async def mutate(
            path: str, content: str, mode: str, expected_hash: str | None
        ) -> str:
            calls.append((path, content, mode, expected_hash))
            return f"Created '{path}'."

        scoped = SearchPath(path=tmp_path, scope=WorkspaceScope())
        workspace_tool = replace(
            tool,
            paths=(scoped,),
            writer=WriteDocumentTool(paths=(scoped,), mutator=mutate),
        )
        result = await workspace_tool(
            script_path="~/script.py",
            input_paths=("~/input.txt",),
            output_path="~/output.txt",
        )

        assert result.data == PythonResult(
            result="5",
            script_path="~/script.py",
            written_file="~/output.txt",
        )
        assert calls == [("~/output.txt", "HELLO", "create", None)]

    async def test_stored_script_is_reloaded_after_an_edit(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        script = tmp_path / "script.py"
        scoped = SearchPath(path=tmp_path, scope=WorkspaceScope())
        workspace_tool = replace(tool, paths=(scoped,))

        script.write_text("1 + 1")
        first = await workspace_tool(script_path="~/script.py")
        script.write_text("1 + 2")
        second = await workspace_tool(script_path="~/script.py")

        assert first.data.result == "2"
        assert second.data.result == "3"

    async def test_input_change_is_discarded_without_a_declared_output(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        source = tmp_path / "source.txt"
        source.write_text("old")
        scoped = SearchPath(path=tmp_path, scope=WorkspaceScope())
        workspace_tool = replace(tool, paths=(scoped,))

        await workspace_tool(
            code=(
                'from pathlib import Path\n'
                'Path("/workspace/~/source.txt").write_text("new")'
            ),
            input_paths=("~/source.txt",),
        )

        assert source.read_text() == "old"

    async def test_existing_output_uses_its_content_hash(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        output = tmp_path / "output.txt"
        output.write_text("old")
        calls: list[tuple[str, str, str, str | None]] = []

        async def mutate(
            path: str, content: str, mode: str, expected_hash: str | None
        ) -> str:
            calls.append((path, content, mode, expected_hash))
            return f"Wrote '{path}'."

        scoped = SearchPath(path=tmp_path, scope=WorkspaceScope())
        workspace_tool = replace(
            tool,
            paths=(scoped,),
            writer=WriteDocumentTool(paths=(scoped,), mutator=mutate),
        )
        await workspace_tool(
            code=(
                'from pathlib import Path\n'
                'Path("/workspace/~/output.txt").write_text("new")'
            ),
            output_path="~/output.txt",
        )

        assert calls == [
            ("~/output.txt", "new", "replace", content_hash("old"))
        ]

    async def test_empty_inputs_and_outputs_remain_present(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "input.txt").write_text("")
        (tmp_path / "output.txt").write_text("")
        workspace_tool, calls = _recording_python_tool(tool, tmp_path)
        await workspace_tool(
            code=(
                "from pathlib import Path\n"
                'source = Path("/workspace/~/input.txt")\n'
                'output = Path("/workspace/~/output.txt")\n'
                "assert source.exists() and source.read_text() == ''\n"
                "assert output.exists() and output.read_text() == ''\n"
                'output.write_text("new")'
            ),
            input_paths=("~/input.txt",),
            output_path="~/output.txt",
        )

        assert calls == [
            ("~/output.txt", "new", "replace", content_hash(""))
        ]

    async def test_file_renamed_to_output_is_persisted(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        workspace_tool, calls = _recording_python_tool(tool, tmp_path)
        await workspace_tool(
            code=(
                "from pathlib import Path\n"
                'temporary = Path("/workspace/~/temporary.txt")\n'
                'temporary.write_text("final")\n'
                'temporary.rename("/workspace/~/output.txt")'
            ),
            output_path="~/output.txt",
        )

        assert calls == [("~/output.txt", "final", "create", None)]

    async def test_output_renamed_away_is_not_persisted(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        output = tmp_path / "output.txt"
        output.write_text("old")
        workspace_tool, calls = _recording_python_tool(tool, tmp_path)

        with pytest.raises(ToolRetry, match="deleted or renamed"):
            await workspace_tool(
                code=(
                    "from pathlib import Path\n"
                    'Path("/workspace/~/output.txt").rename('
                    '"/workspace/~/moved.txt")'
                ),
                output_path="~/output.txt",
            )

        assert calls == []

    async def test_a_path_given_as_both_input_and_output_is_staged_once(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "notes.txt").write_text("old")
        calls: list[tuple[str, str, str, str | None]] = []

        async def mutate(
            path: str, content: str, mode: str, expected_hash: str | None
        ) -> str:
            calls.append((path, content, mode, expected_hash))
            return f"Wrote '{path}'."

        scoped = SearchPath(path=tmp_path, scope=WorkspaceScope())
        workspace_tool = replace(
            tool,
            paths=(scoped,),
            writer=WriteDocumentTool(paths=(scoped,), mutator=mutate),
        )
        result = await workspace_tool(
            code=(
                "from pathlib import Path\n"
                'note = Path("/workspace/~/notes.txt")\n'
                'note.write_text(note.read_text() + " new")\n'
                "note.read_text()"
            ),
            input_paths=("~/notes.txt",),
            output_path="~/notes.txt",
        )

        assert result.data.result == "'old new'"
        assert calls == [("~/notes.txt", "old new", "replace", content_hash("old"))]

    async def test_failed_program_does_not_persist_output(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        async def mutate(
            path: str, _content: str, _mode: str, _expected_hash: str | None
        ) -> str:
            calls.append(path)
            return "written"

        scoped = SearchPath(path=tmp_path, scope=WorkspaceScope())
        workspace_tool = replace(
            tool,
            writer=WriteDocumentTool(paths=(scoped,), mutator=mutate),
        )

        with pytest.raises(ToolRetry, match="ZeroDivisionError"):
            await workspace_tool(
                code=(
                    'from pathlib import Path\n'
                    'Path("/workspace/~/output.txt").write_text("new")\n'
                    "1 / 0"
                ),
                output_path="~/output.txt",
            )

        assert calls == []
