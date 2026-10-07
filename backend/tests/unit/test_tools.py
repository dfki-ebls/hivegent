"""Unit tests for shared tool classes and ToolFactory."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter
from pydantic_monty import AsyncMonty

from hivegent.changes import (
    Changeset,
    ChangesetSummary,
    Delete,
    Edit,
    Move,
    TextEdit,
    Write,
)
from hivegent.converters import VISION_MEDIA_TYPES
from hivegent.multimodal import BinaryContentMode
from hivegent.store import WorkspaceScope
from hivegent.tools import binary, workspace_os
from hivegent.tools.base import (
    Batch,
    BatchShare,
    ItemFailure,
    SearchPath,
    ToolOutput,
    ToolRetry,
    resolve_accessible_file,
    run_batch,
    scope_paths,
)
from hivegent.tools.binary import BinaryRead, ReadBinaryDocumentTool
from hivegent.tools.changeset import ChangesetOutcome, PendingChanges
from hivegent.tools.documents import (
    DocumentRange,
    DocumentRead,
    DocumentSummary,
    DocumentTreeNode,
    GlobDocumentsTool,
    ListDocumentsTool,
    ReadDocumentTool,
)
from hivegent.tools.grep import GrepLine, GrepMatch, GrepTool
from hivegent.tools.jq import JqResult, JqTool
from hivegent.tools.mutations import (
    DeleteDocumentsTool,
    DocumentMove,
    EditDocumentTool,
    MoveDocumentsTool,
    WriteDocumentTool,
)
from hivegent.tools.python import PythonResult, RunPythonTool
from hivegent.tools.sink import OutputSink, RedirectedOutput
from hivegent.tools.table import QueryTableTool
from hivegent.types import DocumentFilter
from tests.helpers import LIMITS, returned, single


def _as_summaries(
    data: list[DocumentSummary] | DocumentTreeNode | RedirectedOutput,
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


def test_path_tool_caches_coercion_without_mutating_input(tmp_path: Path) -> None:
    """A flexible constructor input becomes one lazily cached path tuple."""
    tool = ReadDocumentTool(paths=tmp_path)

    assert tool.resolved_paths is tool.resolved_paths
    assert tool.paths == tmp_path


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

    async def test_listing_subdirectory_cannot_escape_its_filter(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "excluded.md").write_text("secret")
        (tmp_path / "sub").mkdir()
        tool = GlobDocumentsTool(paths=(self._scoped(tmp_path),))

        with pytest.raises(ToolRetry, match="does not exist"):
            await tool(["*.md"], path="~/sub/..")

    async def test_listing_subdirectory_cannot_escape_workspace(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret")
        tool = ListDocumentsTool(
            paths=(SearchPath(path=workspace, scope=WorkspaceScope()),)
        )

        with pytest.raises(ToolRetry, match="does not exist"):
            await tool(path="~/../outside", max_depth=None)

    async def test_listing_subdirectory_cannot_follow_escaping_symlink(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret")
        (workspace / "alias").symlink_to(outside, target_is_directory=True)
        tool = ListDocumentsTool(
            paths=(SearchPath(path=workspace, scope=WorkspaceScope()),)
        )

        with pytest.raises(ToolRetry, match="does not exist"):
            await tool(path="~/alias", max_depth=None)


class TestListDocumentsTool:
    """Tests for ListDocumentsTool (flat list and tree modes)."""

    # --- Flat list mode tests (default) ---

    async def test_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        assert (await tool()).data == []

    async def test_lists_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = _as_summaries((await tool()).data)
        filenames = [r.filename for r in data]
        assert "a.md" in filenames
        assert "b.txt" not in filenames

    @pytest.mark.parametrize("path", [".", "./", "~/."])
    async def test_root_directory_aliases(self, tmp_path: Path, path: str) -> None:
        (tmp_path / "a.md").write_text("hello")
        paths = (SearchPath(path=tmp_path, scope=WorkspaceScope()),)

        data = _as_summaries((await ListDocumentsTool(paths=paths)(path=path)).data)
        matches = (await GlobDocumentsTool(paths=paths)(["*.md"], path=path)).data

        assert [entry.filename for entry in data] == ["~/a.md"]
        assert matches == ["~/a.md"]

    async def test_missing_subdirectory_is_retry(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(
            paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        with pytest.raises(ToolRetry, match="Directory '~/missing' does not exist"):
            await tool(path="~/missing")

    async def test_subdirectory_traversal_is_folded(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("hello")
        tool = ListDocumentsTool(
            paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        data = _as_summaries((await tool(path="~/sub/../docs")).data)

        assert [entry.filename for entry in data] == ["~/docs/a.md"]

    async def test_unknown_prefix_names_the_roots(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(
            paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        with pytest.raises(ToolRetry, match="This tool addresses ~"):
            await tool(path="@nope/x")

    async def test_empty_listing_names_its_scope(self, tmp_path: Path) -> None:
        (tmp_path / "doc.assets").mkdir()
        (tmp_path / "doc.assets" / "img.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(
            paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        empty = await tool(path="~/doc.assets", max_depth=None)

        assert empty.data == []
        assert empty.formatted == (
            "(no documents under '~/doc.assets')\n\n[1 hidden entry (`.assets` "
            "contents and common build/vendor directories), pass "
            "include_ignored=True to reveal them]"
        )

    async def test_custom_glob(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.txt")
        data = _as_summaries((await tool()).data)
        filenames = [r.filename for r in data]
        assert "a.txt" in filenames
        assert "b.md" not in filenames

    async def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = _as_summaries((await tool(path="notes")).data)
        filenames = [r.filename for r in data]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    async def test_none_glob_lists_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        (tmp_path / "c.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries((await tool()).data)
        filenames = {r.filename for r in data}
        assert filenames == {"a.md", "b.txt", "c.png"}

    async def test_nonexistent_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path / "nonexistent", glob="*.md")
        assert (await tool()).data == []

    async def test_assets_contents_hidden_unless_ignored_included(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "doc.md").write_text("text")
        assets = tmp_path / "doc.assets"
        assets.mkdir()
        (assets / "img.png").write_bytes(b"\x89PNG")
        tool = ListDocumentsTool(paths=tmp_path)
        filenames = {
            r.filename for r in _as_summaries((await tool(max_depth=None)).data)
        }
        assert filenames == {"doc.md", "doc.assets"}
        revealed = {
            r.filename
            for r in _as_summaries(
                (await tool(max_depth=None, include_ignored=True)).data
            )
        }
        assert "doc.assets/img.png" in revealed

    async def test_multi_store(self, tmp_path: Path) -> None:
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
        data = _as_summaries((await tool()).data)
        filenames = {r.filename for r in data}
        assert filenames == {"a.md", "@team/b.md"}

    async def test_prefix_scopes_to_one_store(self, tmp_path: Path) -> None:
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
        filenames = {r.filename for r in _as_summaries((await tool(path="@team")).data)}
        assert filenames == {"@team/b.md"}

    async def test_includes_directories(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries((await tool(max_depth=None)).data)
        dirs = [r for r in data if r.is_directory]
        assert any(r.filename == "notes" for r in dirs)

    async def test_max_depth_default_excludes_nested(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries((await tool()).data)
        filenames = {r.filename for r in data}
        assert "top.md" in filenames
        assert "notes" in filenames
        assert "notes/n.md" not in filenames

    async def test_max_results_limits_list(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = ListDocumentsTool(paths=tmp_path)
        data = (await tool(max_results=3)).data
        assert isinstance(data, list)
        assert len(data) == 3

    async def test_skips_build_dirs_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries((await tool(max_depth=None)).data)
        filenames = {r.filename for r in data}
        assert "src.py" in filenames
        assert "__pycache__" not in filenames
        assert "__pycache__/junk.pyc" not in filenames

    async def test_include_ignored_exposes_build_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)
        data = _as_summaries((await tool(max_depth=None, include_ignored=True)).data)
        filenames = {r.filename for r in data}
        assert "__pycache__" in filenames

    # --- Tree mode tests (flatten=False) ---

    async def test_tree_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        result = await tool(flatten=False)
        assert isinstance(result.data, DocumentTreeNode)
        assert result.data.children == ()
        assert result.formatted == "(empty tree in the workspace)"

    async def test_empty_result_hint_counts_hidden_entries(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "a.pyc").write_bytes(b"x")
        (cache / "b.pyc").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)
        result = await tool(max_depth=None)
        assert result.data == []
        assert result.formatted is not None
        assert "3 hidden entries" in result.formatted
        assert "include_ignored=True" in result.formatted
        assert (
            await tool(max_depth=None, include_ignored=True)
        ).formatted != result.formatted

    async def test_a_listing_names_what_it_left_out(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("x")
        (tmp_path / "doc.assets").mkdir()
        (tmp_path / "doc.assets" / "a.png").write_bytes(b"x")
        tool = ListDocumentsTool(paths=tmp_path)

        tree = (await tool(flatten=False, max_depth=3)).text
        deep = (await tool(include_ignored=True)).text
        capped = (await tool(max_results=1)).text

        assert "1 hidden entry" in tree
        assert "1 entry below max_depth=1" in deep
        assert "reached max_results=1" in capped

    async def test_tree_single_level(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = (await tool(flatten=False, max_depth=None)).data
        assert isinstance(data, DocumentTreeNode)
        names = [c.name for c in data.children]
        assert names == ["a.md", "b.md"]
        assert all(not c.is_directory for c in data.children)

    async def test_tree_nested_structure(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = (await tool(flatten=False, max_depth=None)).data
        assert isinstance(data, DocumentTreeNode)
        dir_children = [c for c in data.children if c.is_directory]
        assert len(dir_children) == 1
        assert dir_children[0].name == "notes"
        assert dir_children[0].children[0].name == "n.md"

    async def test_tree_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path, glob="*.md")
        data = (await tool(path="notes", flatten=False, max_depth=None)).data
        assert isinstance(data, DocumentTreeNode)
        assert len(data.children) == 1
        assert data.children[0].name == "notes"
        assert data.children[0].children[0].name == "n.md"

    async def test_tree_max_depth(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("deep")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(paths=tmp_path)
        data = (await tool(flatten=False, max_depth=1)).data
        assert isinstance(data, DocumentTreeNode)
        all_names = {c.name for c in data.children}
        assert "top.md" in all_names
        assert "a" in all_names

    async def test_tree_max_results(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = ListDocumentsTool(paths=tmp_path)
        data = (await tool(flatten=False, max_results=3)).data
        assert isinstance(data, DocumentTreeNode)
        assert len(data.children) == 3

    async def test_tree_multi_store(self, tmp_path: Path) -> None:
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
        data = (await tool(flatten=False)).data
        assert isinstance(data, DocumentTreeNode)
        names = {c.name for c in data.children}
        assert names == {"a.md", "@team"}

    async def test_tree_formatted_output(self, tmp_path: Path) -> None:
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path)
        formatted = (await tool(flatten=False, max_depth=None)).formatted
        assert formatted is not None
        assert "├── " in formatted or "└── " in formatted

    async def test_tree_summary_line(self, tmp_path: Path) -> None:
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(paths=tmp_path)
        formatted = (await tool(flatten=False, max_depth=None)).formatted
        assert formatted is not None
        assert "1 directory" in formatted
        assert "2 files" in formatted


class TestGlobDocumentsTool:
    """Tests for GlobDocumentsTool (pattern-based file matching)."""

    async def test_matches_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("a")
        (tmp_path / "readme.md").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path, glob="*.md")
        assert (await tool(["note*"])).data == ["notes.md"]

    async def test_custom_base_glob(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("a")
        (tmp_path / "data.md").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path, glob="*.txt")
        assert (await tool(["*"])).data == ["data.txt"]

    async def test_none_base_matches_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        tool = GlobDocumentsTool(paths=tmp_path)
        data = (await tool(["*"])).data
        assert isinstance(data, list)
        assert set(data) == {"a.md", "b.txt"}

    async def test_multi_store(self, tmp_path: Path) -> None:
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
        data = (await tool(["*.md"])).data
        assert isinstance(data, list)
        assert set(data) == {"a.md", "@team/b.md"}

    async def test_prefix_scopes_to_one_store(self, tmp_path: Path) -> None:
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
        assert (await tool(["*.md"], path="~")).data == ["~/a.md"]

    async def test_max_results(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = GlobDocumentsTool(paths=tmp_path)
        data = (await tool(["*.txt"], max_results=3)).data
        assert isinstance(data, list)
        assert len(data) == 3

    async def test_patterns_are_unioned_and_each_file_listed_once(
        self, tmp_path: Path
    ) -> None:
        for name in ("a.md", "b.txt", "c.csv"):
            (tmp_path / name).write_text(name)

        tool = GlobDocumentsTool(paths=tmp_path)

        data = (await tool(["*.md", "*.txt", "a.*"])).data

        assert data == ["a.md", "b.txt"]

    async def test_subdir_scoping(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "a.md").write_text("a")
        (tmp_path / "top.md").write_text("top")
        tool = GlobDocumentsTool(paths=tmp_path)
        data = (await tool(["*.md"], path="notes")).data
        assert data == ["notes/a.md"]


class TestReadDocumentTool:
    """Tests for ReadDocumentTool (line-range reads with line numbers)."""

    async def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("content here")
        tool = ReadDocumentTool(paths=tmp_path)
        result = single((await tool([DocumentRead("doc.md")])).data)
        assert isinstance(result, DocumentRange)
        assert result.content == "content here"
        assert result.start_line == 1
        assert result.end_line == 1
        assert result.total_lines == 1
        assert result.content_hash  # surfaced for optimistic-concurrency edits

    async def test_reads_file_named_with_a_decomposed_path(
        self, tmp_path: Path
    ) -> None:
        # The production failure: the file is stored precomposed but a model can
        # only emit the decomposed spelling of a path it was shown, and on a
        # normalization-sensitive filesystem the two name different files.
        # Escapes, not literals: this file is saved precomposed, so writing both
        # spellings out would compare NFC with NFC and assert nothing.
        (tmp_path / "S\u00dcVOA.md").write_text("content here")
        tool = ReadDocumentTool(paths=tmp_path)
        result = single((await tool([DocumentRead("SU\u0308VOA.md")])).data)
        assert isinstance(result, DocumentRange)
        assert result.content == "content here"

    async def test_binary_original_directs_to_markdown_sidecar(
        self, tmp_path: Path
    ) -> None:
        # A non-decodable original (report.docx) is never silently swapped; the
        # retry points the model at its <stem>.md sidecar so the read re-runs the
        # normal path resolution instead of following a sibling behind the scenes.
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        (tmp_path / "report.md").write_text("extracted text")
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="report.md"):
            await tool([DocumentRead("report.docx")])

    async def test_projection_of_a_table_names_query_table(
        self, tmp_path: Path
    ) -> None:
        # The read that matters: an uploaded spreadsheet is only ever addressed
        # by its markdown projection, so keying the hint on the requested
        # suffix left query_table invisible for exactly the file it exists for.
        (tmp_path / "lab.xlsx").write_bytes(b"PK\x03\x04\xec\xec binary")
        (tmp_path / "lab.md").write_text("| a | b |\n|---|---|\n| 1 | 2 |")
        tool = ReadDocumentTool(paths=SearchPath(path=tmp_path, scope=WorkspaceScope()))

        result = await tool([DocumentRead("~/lab.md")])

        assert result.formatted is not None
        assert "query_table" in result.formatted
        assert "'~/lab.xlsx'" in result.formatted

    async def test_plain_markdown_names_no_query_tool(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("prose")
        tool = ReadDocumentTool(paths=tmp_path)

        result = await tool([DocumentRead("notes.md")])

        assert result.formatted is not None
        assert "query_table" not in result.formatted

    async def test_binary_table_is_refused_naming_query_table(
        self, tmp_path: Path
    ) -> None:
        # Both halves of the seam: the tool that answers without reading leads,
        # and the extracted text stays named behind it.
        (tmp_path / "lab.xlsx").write_bytes(b"PK\x03\x04\xec\xec binary")
        (tmp_path / "lab.md").write_text("extracted text")
        tool = ReadDocumentTool(paths=tmp_path)

        with pytest.raises(ToolRetry, match=r"query_table.*'lab\.md'"):
            await tool([DocumentRead("lab.xlsx")])

    @pytest.mark.parametrize("suffix", sorted(VISION_MEDIA_TYPES))
    async def test_supported_binary_directs_to_binary_tool(
        self, tmp_path: Path, suffix: str
    ) -> None:
        # A vision-capable binary is sent to read_binary_document, on its name
        # alone. The write gateway refuses the same set from the same table, so
        # the two tools cannot drift into disagreeing about what counts as text
        # (see test_workspace_mutations for the other half).
        (tmp_path / f"scan{suffix}").write_text("text wearing a binary extension")
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="read_binary_document"):
            await tool([DocumentRead(f"scan{suffix}")])

    async def test_binary_tool_points_unshowable_input_at_its_sidecar(
        self, tmp_path: Path
    ) -> None:
        # An Office document is neither showable nor readable as text, so the
        # refusal names the extracted text rather than the tool that would
        # refuse it in turn.
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        tool = ReadBinaryDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="report.md"):
            await tool([BinaryRead("report.docx")])

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
            await tool([BinaryRead("@team/report.docx")])

    async def test_binary_tool_bounds_pdf_pages_by_the_image_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The serving gateway rejects a whole request carrying more images than
        # its per-prompt cap, which would fail the turn, so the reader renders
        # no more pages than the cap admits and the over-request lands as the
        # retryable refusal naming pages=.
        (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n")
        seen: list[int] = []

        async def render(
            raw: bytes, spec: str | None, max_dimension: int, max_pages: int
        ) -> tuple[tuple[bytes, ...], tuple[int, ...]]:
            seen.append(max_pages)
            raise ValueError(
                f"3 pages exceeds the {max_pages}-page limit — narrow with pages="
            )

        monkeypatch.setattr(binary, "render_pdf_pages", render)
        tool = ReadBinaryDocumentTool(paths=tmp_path, max_images=2)

        with pytest.raises(ToolRetry, match="narrow with pages="):
            await tool([BinaryRead("report.pdf")])

        assert seen == [2]

        # Two files share the cap, so each is bounded by its share of it.
        (tmp_path / "other.pdf").write_bytes(b"%PDF-1.4\n")
        seen.clear()
        tool = ReadBinaryDocumentTool(paths=tmp_path, max_images=3)

        with pytest.raises(ToolRetry, match="Every item failed"):
            await tool([BinaryRead("report.pdf"), BinaryRead("other.pdf")])

        assert sorted(seen) == [1, 2]

    async def test_native_pdfs_do_not_spend_the_image_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("a.pdf", "b.pdf", "c.gif"):
            (tmp_path / name).write_bytes(b"content")

        seen: list[int | None] = []

        async def sample(
            _tool: ReadBinaryDocumentTool,
            canonical: str,
            raw: bytes,
            media_type: str,
            cap: int | None,
        ) -> ToolOutput[binary.BinaryReadResult]:
            seen.append(cap)

            return ToolOutput(binary.BinaryReadResult(canonical, media_type, len(raw)))

        monkeypatch.setattr(binary, "animation_frame_count", lambda *_: 2)
        monkeypatch.setattr(ReadBinaryDocumentTool, "_read_animation", sample)
        tool = ReadBinaryDocumentTool(
            paths=tmp_path, binary_content_mode=BinaryContentMode.NATIVE, max_images=1
        )

        result = await tool([BinaryRead("a.pdf"), BinaryRead("b.pdf"), BinaryRead("c.gif")])

        assert [a.media_type for a in result.attachments] == ["application/pdf"] * 2
        assert seen == [1]

    async def test_binary_without_companion_retries(self, tmp_path: Path) -> None:
        # Undecodable bytes become a recoverable ToolRetry, never a run-aborting
        # UnicodeDecodeError.
        (tmp_path / "blob.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xec\xec\xff\xfe")
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not text"):
            await tool([DocumentRead("blob.bin")])

    async def test_legacy_encoding_is_decoded_and_reported(
        self, tmp_path: Path
    ) -> None:
        # A cp1252/UTF-16 original is content, not a binary: it is decoded
        # rather than refused, and the source encoding is named so a wrong
        # guess on short input is visible instead of silent.
        (tmp_path / "settings.ini").write_bytes("Benutzer = Jörg\n".encode("utf-16"))
        tool = ReadDocumentTool(paths=tmp_path)

        result = await tool([DocumentRead("settings.ini")])

        assert isinstance(single(result.data), DocumentRange)
        assert single(result.data).content == "Benutzer = Jörg"
        assert result.formatted is not None
        assert "decoded from utf-16" in result.formatted

    async def test_rejects_nonexistent(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool([DocumentRead("missing.md")])

    async def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool([DocumentRead("../../../etc/passwd")])

    async def test_rejects_a_directory_as_a_directory(self, tmp_path: Path) -> None:
        # A selected folder reaches the model as a path like any other, so it
        # is read like one. "Not found" sent that run hunting for a respelling
        # of a path that was right; the listing tool is the actual correction.
        (tmp_path / "reports").mkdir()
        tool = ReadDocumentTool(paths=tmp_path)

        with pytest.raises(ToolRetry, match="is a directory.*list_documents"):
            await tool([DocumentRead("reports/")])

    async def test_reads_group_document(self, tmp_path: Path) -> None:
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "doc.md").write_text("group content")
        tool = ReadDocumentTool(
            paths=(
                SearchPath(path=tmp_path),
                SearchPath(path=group_dir, scope=WorkspaceScope("team")),
            )
        )
        result = single((await tool([DocumentRead("@team/doc.md")])).data)
        assert isinstance(result, DocumentRange)
        assert result.content == "group content"

    async def test_rejects_unknown_prefix(self, tmp_path: Path) -> None:
        tool = ReadDocumentTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool([DocumentRead("@unknown/doc.md")])

    async def test_formatted_always_includes_line_numbers(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("alpha\nbeta")
        tool = ReadDocumentTool(paths=tmp_path)
        formatted = (await tool([DocumentRead("doc.md")])).formatted
        assert formatted is not None
        assert "1: alpha" in formatted
        assert "2: beta" in formatted

    async def test_char_cap_truncates_within_window(self, tmp_path: Path) -> None:
        content = "x" * 200
        (tmp_path / "big.md").write_text(content)
        tool = ReadDocumentTool(paths=tmp_path, max_chars=50)
        result = single((await tool([DocumentRead("big.md")])).data)
        assert isinstance(result, DocumentRange)
        # Single 200-char line fits in the window so it's kept whole;
        # but a longer file with multiple lines would get clipped.
        assert len(result.content) == 200

    async def test_char_cap_clips_multiline(self, tmp_path: Path) -> None:
        lines = ["y" * 50 for _ in range(10)]
        (tmp_path / "big.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path, max_chars=120)
        result = single((await tool([DocumentRead("big.md")])).data)
        assert isinstance(result, DocumentRange)
        # 120-char budget fits ~2 lines (each 50 + newline = 51 chars).
        assert result.end_line < result.total_lines

    async def test_long_line_truncated_in_formatted_only(self, tmp_path: Path) -> None:
        # A base64-image line the window returns whole (it is the first
        # selected line) must not flood the model context: the formatted
        # output truncates it, while the structured content keeps it intact.
        long_line = "data:image/png;base64," + "A" * 500_000
        (tmp_path / "img.md").write_text(f"{long_line}\ntail")
        tool = ReadDocumentTool(paths=tmp_path, max_line_chars=80)
        out = await tool([DocumentRead("img.md")])
        assert out.formatted is not None
        assert "…" in out.formatted
        assert len(max(out.formatted.splitlines(), key=len)) < 200
        assert isinstance(single(out.data), DocumentRange)
        assert long_line in single(out.data).content

    async def test_tabular_file_is_pointed_at_query_table(self, tmp_path: Path) -> None:
        # The one moment the caller finds out a line read was the wrong tool
        # for this file is when it reads one, so the read says so.
        (tmp_path / "sales.csv").write_text("region,amount\nEU,100")
        tool = ReadDocumentTool(paths=tmp_path)
        formatted = (await tool([DocumentRead("sales.csv")])).formatted

        assert formatted is not None
        assert "query_table" in formatted

    async def test_full_lines_opts_out_of_the_per_line_clip(
        self, tmp_path: Path
    ) -> None:
        # A wide markdown table row loses its trailing columns to the clip with
        # nothing but an ellipsis to show for it, which the reader cannot spot
        # from the output alone: the clip is named, and full_lines undoes it.
        row = "| " + " | ".join(f"col{i}" for i in range(200)) + " |"
        (tmp_path / "table.md").write_text(row)
        tool = ReadDocumentTool(paths=tmp_path, max_line_chars=80)

        clipped = (await tool([DocumentRead("table.md")])).formatted
        assert clipped is not None
        assert "full_lines=true" in clipped
        assert "col199" not in clipped

        whole = (await tool([DocumentRead("table.md")], full_lines=True)).formatted
        assert whole is not None
        assert "col199" in whole
        assert "full_lines=true" not in whole

    async def test_formatted_budget_binds_the_text_and_not_the_result(
        self, tmp_path: Path
    ) -> None:
        # The rendered budget decides what the model saw, not what the call
        # read: trimming the result to it wrote fewer lines to a `.json`
        # redirect than the read had taken, under a receipt for the whole of
        # it.  Where the text stopped is said instead, so a follow-up offset
        # can still resume from the last line actually shown.
        (tmp_path / "doc.md").write_text("\n".join(f"line{i}" for i in range(100)))
        tool = ReadDocumentTool(paths=tmp_path, max_formatted_chars=40)
        out = await tool([DocumentRead("doc.md")])

        assert isinstance(single(out.data), DocumentRange)
        assert single(out.data).end_line == 100
        assert single(out.data).content.splitlines() == [f"line{i}" for i in range(100)]
        assert out.formatted is not None

        shown = re.search(r"the text above stops at line (\d+)", out.formatted)
        assert shown is not None
        assert 0 < int(shown[1]) < 100
        assert f"offset={int(shown[1]) + 1}" in out.formatted

    async def test_reads_several_documents_each_under_its_own_key(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("\n".join(f"a{i}" for i in range(10)))
        tool = ReadDocumentTool(paths=tmp_path)

        out = await returned(
            tool([DocumentRead("a.md", offset=3, limit=2), DocumentRead("b.md")])
        )

        first, second = out.data
        assert isinstance(first, DocumentRange)
        assert (first.file_path, first.content) == ("a.md", "a2\na3")
        assert second == ItemFailure("b.md", "'b.md' not found.")
        assert "==> a.md (offset=3, limit=2) <==" in out.text
        assert "offset=5" in out.text

    # --- offset / limit tests ---

    async def test_correct_range(self, tmp_path: Path) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5"]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path)
        result = single((await tool([DocumentRead("doc.md", offset=2, limit=3)])).data)
        assert isinstance(result, DocumentRange)
        assert result.start_line == 2
        assert result.end_line == 4
        assert result.total_lines == 5
        assert result.content == "line2\nline3\nline4"

    async def test_defaults_to_full_file_when_small(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = ReadDocumentTool(paths=tmp_path)
        result = single((await tool([DocumentRead("doc.md")])).data)
        assert isinstance(result, DocumentRange)
        assert result.start_line == 1
        assert result.end_line == 3
        assert result.content == "a\nb\nc"

    async def test_offset_without_limit(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = ReadDocumentTool(paths=tmp_path)
        result = single((await tool([DocumentRead("doc.md", offset=2)])).data)
        assert isinstance(result, DocumentRange)
        assert result.start_line == 2
        assert result.end_line == 3
        assert result.content == "b\nc"

    async def test_default_window_caps_lines(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(5000)]
        (tmp_path / "big.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path)
        result = single((await tool([DocumentRead("big.md")])).data)
        assert isinstance(result, DocumentRange)
        assert result.start_line == 1
        assert result.end_line == 2000
        assert result.total_lines == 5000

    async def test_custom_default_lines(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(100)]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path, default_lines=10)
        result = single((await tool([DocumentRead("doc.md")])).data)
        assert isinstance(result, DocumentRange)
        assert result.end_line == 10

    async def test_continuation_hint_when_truncated(self, tmp_path: Path) -> None:
        lines = [f"line{i}" for i in range(100)]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = ReadDocumentTool(paths=tmp_path, default_lines=10)
        formatted = (await tool([DocumentRead("doc.md")])).formatted
        assert formatted is not None
        assert "more lines" in formatted
        assert "offset=11" in formatted


class TestRunBatch:
    """The list-first contract every batched tool is built on."""

    @staticmethod
    async def _serve(item: str, share: BatchShare) -> ToolOutput[str]:
        """Refuse a `bad` item, and finish the first item last."""
        await asyncio.sleep(0.01 if share.index == 0 else 0)

        if item.startswith("bad"):
            raise ToolRetry(f"{item} refused")

        return ToolOutput(data=f"{item}:{share.of(10)}")

    async def test_dedupes_and_keeps_request_order_with_split_budgets(self) -> None:
        out = await run_batch(["c", "a", "c", "b"], self._serve, key=str, concurrency=3)

        assert out.data == ("c:4", "a:3", "b:3")
        assert out.text == "==> c <==\nc:4\n\n==> a <==\na:3\n\n==> b <==\nb:3"

    async def test_a_failed_item_is_reported_in_place(self) -> None:
        out = await run_batch(["bad", "a"], self._serve, key=str)

        assert out.data == (ItemFailure("bad", "bad refused"), "a:5")
        assert "==> bad <==\nfailed: bad refused" in out.text
        assert TypeAdapter(Batch[str]).dump_python(out.data, mode="json") == [
            {"item": "bad", "reason": "bad refused", "kind": "failure"},
            "a:5",
        ]

    async def test_only_a_batch_with_every_item_failed_is_refused(self) -> None:
        with pytest.raises(ToolRetry, match="^bad refused$"):
            await run_batch(["bad"], self._serve, key=str)

        with pytest.raises(ToolRetry, match="Every item failed:\n- bad1: bad1"):
            await run_batch(["bad1", "bad2"], self._serve, key=str)


class TestEditDocumentTool:
    """The tool resolves and access-checks the path, then commits one edit.

    The edit algorithm itself lives in the changeset gateway and is covered by
    ``TestEditDocumentText``.
    """

    async def test_commits_every_edit_as_one(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _commit(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "edited"

        edits = [TextEdit("hello", "goodbye", True), TextEdit("a", "b")]
        tool = EditDocumentTool(paths=tmp_path, commit=_commit)
        result = (await tool("doc.md", edits, expected_hash="h")).data
        assert result == "edited"
        assert calls == [Changeset((Edit("doc.md", tuple(edits), "h"),))]

    async def test_rejects_inaccessible_path(self, tmp_path: Path) -> None:
        tool = EditDocumentTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="not accessible"):
            await tool("../escape.md", [TextEdit("a", "b")])

    async def test_translates_a_refused_commit(self, tmp_path: Path) -> None:
        async def _commit(*_: object) -> str:
            raise HTTPException(status_code=404, detail="Document not found")

        tool = EditDocumentTool(paths=tmp_path, commit=_commit)

        with pytest.raises(ToolRetry, match="Document not found"):
            await tool("doc.md", [TextEdit("a", "b")])


async def _echo_write(changeset: Changeset[str]) -> str:
    """A commit that names the document it was given."""
    return f"wrote {changeset.locations[0]}"


class TestWriteDocumentTool:
    """The tool resolves and access-checks the path, then commits one write.

    The write algorithm itself lives in the changeset gateway and is
    covered by ``TestWriteDocumentText``.
    """

    async def test_commits_one_write(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _commit(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "written"

        tool = WriteDocumentTool(paths=tmp_path, commit=_commit)
        result = (
            await tool("doc.md", "content", mode="append", expected_hash="h")
        ).data
        assert result == "written"
        assert calls == [Changeset((Write("doc.md", "content", "append", "h"),))]

    async def test_a_row_of_the_wrong_width_is_refused(self, tmp_path: Path) -> None:
        # A summary row written with the separators counted by hand puts its
        # value under the wrong heading, which nothing downstream reports.
        tool = WriteDocumentTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="line 3 has 2 fields"):
            await tool("t.csv", "date,load,ew\n2023-01-01,1,2\n,mean: 3\n")

    async def test_an_even_table_is_written(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path, commit=_echo_write)

        result = await tool("t.csv", "date,load,ew\n2023-01-01,1,2\n,,3\n")

        assert result.data

    async def test_the_separator_comes_from_the_suffix(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="line 2 has 4 fields"):
            await tool("t.tsv", "a\tb\tc\n1\t2\t3\t4\n")

    async def test_a_semicolon_file_named_csv_is_one_column(
        self, tmp_path: Path
    ) -> None:
        # A `.csv` is comma-separated by the name it was given, so this is one
        # column with no width to disagree with — and query_table reads it back
        # the same way, which is the point of sharing one separator table.
        tool = WriteDocumentTool(paths=tmp_path, commit=_echo_write)

        assert (await tool("t.csv", "a;b;c\n1;2;3;4\n")).data

    async def test_a_languages_word_for_missing_is_not_a_cell(
        self, tmp_path: Path
    ) -> None:
        # An f-string handed the value rather than the text for it, and the gap
        # reads downstream as the four-letter word instead.
        tool = WriteDocumentTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="'None'"):
            await tool("t.csv", "date,load\n2023-01-01,1\n2023-01-02,None\n")

    async def test_a_lab_sentinel_is_left_alone(self, tmp_path: Path) -> None:
        # `N/A` is written on purpose; `None` never is.
        tool = WriteDocumentTool(paths=tmp_path, commit=_echo_write)

        assert (await tool("t.csv", "date,load\n2023-01-01,N/A\n")).data

    async def test_prose_is_not_a_table(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path, commit=_echo_write)

        assert (await tool("notes.md", "a line\nand, another\n")).data

    async def test_any_text_document_is_written(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(paths=tmp_path, commit=_echo_write)
        result = (await tool("data.txt", "content")).data
        assert result == "wrote data.txt"

    async def test_translates_a_refused_commit(self, tmp_path: Path) -> None:
        async def _mutate(*_: object) -> str:
            raise HTTPException(status_code=400, detail="Unsupported write mode: x")

        tool = WriteDocumentTool(paths=tmp_path, commit=_mutate)

        with pytest.raises(ToolRetry, match="Unsupported write mode"):
            await tool("doc.md", "content")

    async def test_binary_format_is_refused_before_the_commit_runs(
        self, tmp_path: Path
    ) -> None:
        tool = WriteDocumentTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="binary format"):
            await tool("sheet.xlsx", "a,b")

    async def test_text_formats_a_converter_claims_are_writable(
        self, tmp_path: Path
    ) -> None:
        tool = WriteDocumentTool(paths=tmp_path, commit=_echo_write)

        for name in ("rows.csv", "page.html", "diagram.svg"):
            assert (await tool(name, "x")).data == f"wrote {name}"


class TestMoveDocumentsTool:
    """The tool resolves both ends of every move, then delegates the batch."""

    async def test_delegates_every_move_at_once(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _move(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "moved"

        (tmp_path / "old.md").write_text("x")
        (tmp_path / "images").mkdir()
        tool = MoveDocumentsTool(paths=tmp_path, commit=_move)
        result = await tool(
            [DocumentMove("old.md", "notes/new.md"), DocumentMove("images", "notes")]
        )

        assert calls == [
            Changeset((Move("old.md", "notes/new.md"), Move("images", "notes")))
        ]
        assert result.data == "moved"

    async def test_a_missing_source_refuses_the_whole_batch(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "old.md").write_text("x")
        tool = MoveDocumentsTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="'gone.md' not found"):
            await tool(
                [DocumentMove("old.md", "new.md"), DocumentMove("gone.md", "b.md")]
            )

    async def test_translates_a_refused_commit(self, tmp_path: Path) -> None:
        async def _move(*_: object) -> str:
            raise HTTPException(status_code=409, detail="Destination already exists")

        (tmp_path / "old.md").write_text("x")
        tool = MoveDocumentsTool(paths=tmp_path, commit=_move)

        with pytest.raises(ToolRetry, match="Destination already exists"):
            await tool([DocumentMove("old.md", "new.md")])


class TestDeleteDocumentsTool:
    """The tool resolves every existing path, then delegates the batch."""

    async def test_delegates_every_path_at_once(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _delete(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "deleted"

        (tmp_path / "doc.md").write_text("x")
        (tmp_path / "state.json").write_text("{}")
        tool = DeleteDocumentsTool(paths=tmp_path, commit=_delete)

        assert (await tool(["doc.md", "state.json"])).data == "deleted"
        assert calls == [Changeset((Delete("doc.md"), Delete("state.json")))]

    async def test_refuses_a_directory_and_a_missing_document(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "notes").mkdir()
        tool = DeleteDocumentsTool(paths=tmp_path, commit=_unreachable)

        with pytest.raises(ToolRetry, match="is a directory"):
            await tool(["notes"])

        with pytest.raises(ToolRetry, match="not found"):
            await tool(["gone.md"])


async def _unreachable(*_: object) -> Any:
    """Every commit a path-rejection test hands its tool: it must not run."""
    raise AssertionError("commit must not run when the path is rejected")


class TestJqTool:
    """Tests for JqTool."""

    @staticmethod
    def _result(output: Batch[JqResult] | RedirectedOutput) -> JqResult:
        return single(output)

    async def test_filter_selects_values(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"title": "Hello", "n": 42}))
        tool = JqTool(paths=tmp_path)
        result = self._result((await tool(["item.json"], ".title")).data)
        assert result.values == ("Hello",)

    async def test_missing_filter_reports_the_shape(self, tmp_path: Path) -> None:
        """The cheap first call: keys and types, never the document itself."""
        (tmp_path / "item.json").write_text(json.dumps({"title": "Hello", "n": 42}))
        tool = JqTool(paths=tmp_path)
        output = await tool(["item.json"])
        result = self._result(output.data)
        assert result.values == ({"title": "string", "n": "number"},)
        assert "Hello" not in output.text

    async def test_non_json_document_is_turned_away(self, tmp_path: Path) -> None:
        """The suffix table decides, so the reader and the filter cannot disagree."""
        (tmp_path / "notes.md").write_text("# notes")
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not a JSON document"):
            await tool(["notes.md"], ".")

    async def test_invalid_jq_expression(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"x": 1}))
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry):
            await tool(["item.json"], "invalid [[[")

    async def test_malformed_document_is_correctable(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text("{not json")
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="jq failed"):
            await tool(["item.json"], ".")

    async def test_nonexistent_file_path(self, tmp_path: Path) -> None:
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool(["missing.json"], ".")

    async def test_path_traversal(self, tmp_path: Path) -> None:
        tool = JqTool(paths=tmp_path)
        with pytest.raises(ToolRetry, match="not found"):
            await tool(["../etc/passwd"], ".")

    async def test_output_budget_cuts_whole_values(self, tmp_path: Path) -> None:
        """What the budget drops is values, so no JSON comes back cut mid-token."""
        (tmp_path / "big.json").write_text(json.dumps(["x" * 100 for _ in range(50)]))
        tool = JqTool(paths=tmp_path, max_formatted_chars=250)
        output = await tool(["big.json"], ".[]")
        result = self._result(output.data)

        rendered = [line for line in output.text.splitlines() if line.startswith('"')]

        assert 0 < len(rendered) < 50
        assert all(line == '"' + "x" * 100 + '"' for line in rendered)
        assert result.values == tuple("x" * 100 for _ in range(50))

    async def test_output_budget_omits_one_oversized_value(
        self, tmp_path: Path
    ) -> None:
        """One value cannot bypass the jq-specific output budget."""
        (tmp_path / "big.json").write_text(json.dumps({"body": "x" * 500}))
        tool = JqTool(paths=tmp_path, max_formatted_chars=100)
        output = await tool(["big.json"], ".")
        result = self._result(output.data)

        assert "(no values)" in output.text
        assert len(output.text) < 200
        assert result.values == ({"body": "x" * 500},)

    async def test_json_redirect_preserves_values_omitted_from_display(
        self, tmp_path: Path
    ) -> None:
        """The structured redirect stores all jq values, not the display slice."""
        written: dict[str, str] = {}

        async def mutate(changeset: Changeset[str]) -> str:
            (write,) = changeset.operations
            assert isinstance(write, Write)
            written[write.target] = write.content

            return f"wrote {write.target}"

        (tmp_path / "big.json").write_text(json.dumps(list(range(100))))
        writer = WriteDocumentTool(paths=tmp_path, commit=mutate)
        tool = JqTool(
            paths=tmp_path, sink=OutputSink(writer, 0), max_formatted_chars=20
        )

        output = await tool(["big.json"], ".[]", output_path="result.json")
        stored = json.loads(written["result.json"])

        assert isinstance(output.data, RedirectedOutput)
        assert stored[0]["values"] == list(range(100))

    async def test_a_receipt_says_when_what_it_wrote_was_already_cut(
        self, tmp_path: Path
    ) -> None:
        # A redirect hands back a size and nothing else, so a cut the tool knew
        # about dies in the receipt unless it is carried: a run that redirected
        # a capped query and computed from the file could not tell.
        async def mutate(_changeset: Changeset[str]) -> str:
            return "wrote it"

        rows = "\n".join(f"r{i},{i}" for i in range(50))
        (tmp_path / "t.csv").write_text(f"name,val\n{rows}")
        writer = WriteDocumentTool(paths=tmp_path, commit=mutate)
        tool = QueryTableTool(paths=tmp_path, sink=OutputSink(writer, 0), max_rows=10)

        output = await tool(
            ["t.csv"], ["SELECT * FROM t"], row_limit=10, output_path="out.json"
        )

        assert isinstance(output.data, RedirectedOutput)
        assert output.data.truncated
        assert "cut short of what you asked for" in output.text

    async def test_a_whole_result_is_not_called_partial(self, tmp_path: Path) -> None:
        async def mutate(_changeset: Changeset[str]) -> str:
            return "wrote it"

        (tmp_path / "t.csv").write_text("name,val\na,1\nb,2\n")
        writer = WriteDocumentTool(paths=tmp_path, commit=mutate)
        tool = QueryTableTool(paths=tmp_path, sink=OutputSink(writer, 0))

        output = await tool(["t.csv"], ["SELECT * FROM t"], output_path="out.json")

        assert isinstance(output.data, RedirectedOutput)
        assert not output.data.truncated
        assert "cut short" not in output.text


class TestGrepSearch:
    """Tests for GrepTool against real files on disk."""

    @staticmethod
    def _filenames(output: list[GrepMatch] | RedirectedOutput) -> set[str]:
        assert isinstance(output, list)
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

    async def test_a_glob_naming_a_binary_format_is_refused(
        self, tmp_path: Path
    ) -> None:
        # ripgrep skips a binary file without a word, so this search answers
        # "(no matches)" — a clean negative that is really a file never opened,
        # and one a run reasonably takes for proof that the term is absent.
        with pytest.raises(ToolRetry, match="query_table"):
            await GrepTool(paths=tmp_path)("TKN", glob="*.xlsx")

    async def test_a_text_glob_still_searches(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("TKN appears here\n")

        result = await GrepTool(paths=tmp_path)("TKN", glob="*.md")

        assert self._filenames(result.data) == {"notes.md"}

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

    async def test_a_directory_glob_searches_that_subtree_of_every_workspace(
        self, tmp_path: Path
    ) -> None:
        # ripgrep matches a glob carrying a slash relative to its working
        # directory, so one anchored run per workspace is what makes
        # `reports/*.md` name that folder in each of them.  Handed the root as
        # an argument instead, it was tested against the absolute path and
        # matched nothing at all, while a slashless `*.md` appeared to work.
        for name in ("user", "group"):
            reports = tmp_path / name / "reports"
            reports.mkdir(parents=True)
            (reports / "q1.md").write_text("needle\n")
            (tmp_path / name / "loose.md").write_text("needle\n")
        paths = (
            SearchPath(path=tmp_path / "user", scope=WorkspaceScope()),
            SearchPath(path=tmp_path / "group", scope=WorkspaceScope("team")),
        )

        both = await GrepTool(paths=paths)("needle", glob="reports/*.md")
        one = await GrepTool(paths=paths)("needle", glob="~/reports/*.md")

        assert self._filenames(both.data) == {
            "~/reports/q1.md",
            "@team/reports/q1.md",
        }
        assert self._filenames(one.data) == {"~/reports/q1.md"}

    async def test_assets_payload_hidden_unless_ignored(self, tmp_path: Path) -> None:
        assets = tmp_path / "doc.assets"
        assets.mkdir()
        (assets / "fig1.txt").write_text("needle\n")
        hidden = await GrepTool(paths=tmp_path)("needle")
        assert hidden.data == []
        assert "1 hidden entry" in hidden.text
        revealed = await returned(
            GrepTool(paths=tmp_path)("needle", include_ignored=True)
        )
        assert self._filenames(revealed.data) == {"doc.assets/fig1.txt"}

    async def test_a_capped_result_says_how_many_it_left_out(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("needle\n")
        (tmp_path / "b.md").write_text("needle\n")

        result = await GrepTool(paths=tmp_path)(
            "needle", max_results=1, output_mode="files_with_matches"
        )

        assert "showing 1 of 2" in result.text

    async def test_parent_glob_cannot_search_outside_workspace(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "inside.md").write_text("inside\n")
        (tmp_path / "secret.md").write_text("needle\n")
        tool = GrepTool(paths=(SearchPath(path=workspace, scope=WorkspaceScope()),))

        result = await tool("needle", glob="../*.md")

        assert result.data == []


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
        formatted, _hints = tool._format_matches([block])
        assert formatted == "doc.md\n10-ctx\n11:hit"

    def test_oversized_block_truncates_instead_of_dropping(
        self, tmp_path: Path
    ) -> None:
        # A single merged block larger than the budget must still show its
        # leading lines (truncated), never just a heading with no content.
        tool = GrepTool(paths=tmp_path, max_formatted_chars=40)
        formatted, hints = tool._format_matches([self._block(50)])
        shown = formatted.count(":x")
        assert 0 < shown < 50
        assert formatted.startswith("f.md\n1:x")
        assert not formatted.startswith("\n---\n")
        assert hints[0].startswith(f"{50 - shown} of 50 lines omitted")

    def test_fully_shown_has_no_omitted_notice(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted, hints = tool._format_matches([self._block(3)])
        assert formatted == "f.md\n1:x\n2:x\n3:x"
        assert hints == []

    def test_blocks_within_document_separated_by_dashes(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted, _hints = tool._format_matches([self._block(1), self._block(1)])
        assert formatted == "f.md\n1:x\n--\n1:x"

    async def test_separate_documents_joined_by_separator(self, tmp_path: Path) -> None:
        tool = GrepTool(paths=tmp_path, max_formatted_chars=10_000)
        formatted, hints = tool._format_matches(
            [self._block(1, "a.md"), self._block(1, "b.md")]
        )
        assert formatted == "a.md\n1:x\n---\nb.md\n1:x"
        assert hints == []


def _recording_python_tool(
    tool: RunPythonTool, workspace: Path
) -> tuple[RunPythonTool, list[Changeset[str]]]:
    """Mount *workspace* writable and return what each run stages."""
    staged: list[Changeset[str]] = []

    async def commit(changes: Changeset[str]) -> ChangesetOutcome:
        staged.append(changes)

        return PendingChanges("staged-id", ChangesetSummary())

    scoped = SearchPath(path=workspace, scope=WorkspaceScope())
    configured = replace(tool, paths=(scoped,), writable=(scoped,), commit=commit)

    return configured, staged


class TestRunPythonTool:
    """Tests for RunPythonTool."""

    @pytest.fixture()
    async def tool(self) -> AsyncIterator[RunPythonTool]:
        async with AsyncMonty(min_processes=1) as pool:
            yield RunPythonTool(pool=pool, changeset_limits=LIMITS)

    async def test_returns_value_and_printed_output(self, tool: RunPythonTool) -> None:
        result = await tool("import math\nprint('working')\nmath.factorial(5)")
        assert result.data == PythonResult(result="120", stdout="working")
        assert result.text == "working\nResult: 120"

    async def test_blank_code_beside_a_script_runs_the_script(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        """An argument a model spelled empty is the one it did not use."""
        (tmp_path / "run.py").write_text("print('from the script')\n7")
        configured = replace(
            tool, paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        result = await configured(code="", script_path="~/run.py")

        assert result.data.result == "7"
        assert result.data.stdout == "from the script"

    async def test_the_environment_mirrors_a_shell(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        configured = replace(
            tool, environ={"HOME": "/workspace/~", "USER": "u", "TMPDIR": "/tmp"}
        )

        result = await configured(
            "import os\n"
            "[os.getenv(k) for k in ('HOME', 'USER', 'PWD', 'TMPDIR', 'LANG')]"
            " + [os.getcwd()]"
        )

        assert result.data.result == (
            "['/workspace/~', 'u', '/workspace', '/tmp', 'C.UTF-8', '/workspace']"
        )

    async def test_an_empty_program_says_so(self, tool: RunPythonTool) -> None:
        with pytest.raises(ToolRetry, match="program is empty"):
            await tool(code="   ")

    async def test_two_programs_are_refused(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        configured = replace(
            tool, paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )
        # Named apart from the empty case, since a model told to "provide one
        # of" what it just provided both of looks for the fault elsewhere.
        with pytest.raises(ToolRetry, match="both given"):
            await configured(code="1", script_path="~/run.py")

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
        bounded = replace(tool, limits={"max_feed_duration_secs": 0.2})
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

    async def test_oversized_document_is_refused_before_it_is_decoded(
        self, tool: RunPythonTool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The budget bounds host memory, so a file that cannot fit it must never
        # reach the decoder: a program with the workspace mounted can open every
        # document in it, one after the other.
        (tmp_path / "big.txt").write_text("x" * 1000)
        monkeypatch.setattr(
            workspace_os,
            "read_text_file",
            lambda *args, **kwargs: pytest.fail("the document was decoded"),
        )
        bounded = replace(
            tool,
            paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),),
            max_document_chars=200,
        )

        result = await bounded(
            "from pathlib import Path\n"
            "try:\n"
            '    Path("~/big.txt").read_text()\n'
            "except MemoryError as exc:\n"
            "    refusal = str(exc)\n"
            "refusal"
        )

        assert "~/big.txt" in str(result.data.result)

    async def test_stored_script_reads_the_mount_and_stages_its_writes(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "script.py").write_text(
            "from pathlib import Path\n"
            'text = Path("~/input.txt").read_text()\n'
            'Path("~/output.txt").write_text(text.upper())\n'
            'Path("~/output.txt").read_text()'
        )
        (tmp_path / "input.txt").write_text("hello")
        workspace_tool, staged = _recording_python_tool(tool, tmp_path)

        result = await workspace_tool(script_path="~/script.py")

        assert [changes.operations for changes in staged] == [
            (Write("~/output.txt", "HELLO", "create"),)
        ]
        assert result.data.result == "'HELLO'"
        assert result.data.changeset == PendingChanges("staged-id", ChangesetSummary())
        assert "changeset_id='staged-id'" in result.text
        assert not (tmp_path / "output.txt").exists()

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

    async def test_program_discovers_a_document_it_was_never_told_about(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "one.md").write_text("alpha")
        (tmp_path / "notes" / "two.md").write_text("beta")
        workspace_tool = replace(
            tool, paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        result = await workspace_tool(
            "from pathlib import Path\n"
            'sorted(p.read_text() for p in Path("~/notes").iterdir())'
        )

        assert result.data.result == "['alpha', 'beta']"

    async def test_renames_and_removals_stage_through_normal_path_calls(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "inbox").mkdir()

        for name in ("a", "b"):
            (tmp_path / "inbox" / f"{name}.md").write_text(name)

        (tmp_path / "stale.md").write_text("old")
        workspace_tool, staged = _recording_python_tool(tool, tmp_path)

        result = await workspace_tool(
            "from pathlib import Path\n"
            'Path("~/archive").mkdir()\n'
            'for p in Path("~/inbox").iterdir():\n'
            '    p.rename(Path("~/archive") / p.name)\n'
            'Path("~/inbox").rmdir()\n'
            'Path("~/stale.md").unlink()\n'
            '[p.name for p in Path("~").iterdir()]'
        )

        assert result.data.result == "['archive']"
        (changes,) = staged
        assert [type(op) for op in changes.operations] == [Move, Move, Delete, Delete]
        assert changes.locations == (
            "~/inbox/a.md",
            "~/archive/a.md",
            "~/inbox/b.md",
            "~/archive/b.md",
            "~/inbox",
            "~/stale.md",
        )
        assert sorted(p.name for p in tmp_path.iterdir()) == ["inbox", "stale.md"]

    async def test_every_change_is_refused_without_a_writable_span(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        workspace_tool = replace(
            tool, paths=(SearchPath(path=tmp_path, scope=WorkspaceScope()),)
        )

        with pytest.raises(ToolRetry, match="cannot be changed"):
            await workspace_tool(
                'from pathlib import Path\nPath("~/state.json").write_text("{}")'
            )

        assert not (tmp_path / "state.json").exists()

    async def test_a_program_that_changes_nothing_stages_nothing(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        (tmp_path / "notes.md").write_text("same")
        workspace_tool, staged = _recording_python_tool(tool, tmp_path)

        result = await workspace_tool(
            'text = open("~/notes.md").read()\nopen("~/notes.md", "w").write(text)'
        )

        assert staged == []
        assert result.data.changeset is None

    async def test_failed_program_stages_nothing(
        self, tool: RunPythonTool, tmp_path: Path
    ) -> None:
        workspace_tool, staged = _recording_python_tool(tool, tmp_path)

        with pytest.raises(ToolRetry, match="ZeroDivisionError"):
            await workspace_tool(
                'from pathlib import Path\nPath("~/output.txt").write_text("new")\n1 / 0'
            )

        assert staged == []
