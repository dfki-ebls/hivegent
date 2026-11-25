"""Unit tests for the document, search, mutation, and sandbox tools."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from exar.ingest import VISION_MEDIA_TYPES, PageImage, Sample
from exar.memory.mounts import narrow
from exar.text import LineClip
from exar.tools import BatchData, ItemFailure, Retry
from exar.tools.batch import (
    BatchShare,
    serve_batch,
    tool_return,
    unpack,
)
from exar.tools.files import (
    DocumentRange,
    DocumentRead,
    GrepLine,
    GrepMatch,
    JqResult,
)
from exar.tools.files import format_matches as _format_matches
from exar.tools.media import BinaryRead
from pydantic import TypeAdapter
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_monty import AsyncMonty

from hivegent.agents.tools.explore import (
    grep,
    jq,
    list_documents,
    read_binary_document,
    read_document,
)
from hivegent.shared.formats import BinaryContentMode
from hivegent.store import HostGrant, UserScope
from hivegent.tools import workspace_os
from hivegent.tools.base import SearchPath, ToolFunc, mounts_of, resolve_accessible_file
from hivegent.tools.changes import (
    ChangeRefused,
    Changeset,
    ChangesetSummary,
    Delete,
    Edit,
    Move,
    TextEdit,
    Write,
)
from hivegent.tools.changeset import ChangesetOutcome, PendingChanges
from hivegent.tools.mutations import (
    DocumentMove,
    delete_documents,
    edit_document,
    move_documents,
    write_document,
)
from hivegent.tools.python import PythonResult, run_python
from hivegent.types import DocumentFilter, FilterEntry
from hivegent.workspace import Gateway, Root
from tests.helpers import LIMITS, doc, folder, single


class TestNarrow:
    """Tests for `exar.memory.mounts.narrow` over the workspace mounts."""

    MOUNTS = mounts_of(
        (
            SearchPath.of(path=Path("/u"), target="~", implied=True),
            SearchPath.of(path=Path("/g"), target="@team"),
        )
    )

    def test_prefix_scopes_to_one_workspace(self) -> None:
        assert narrow(self.MOUNTS, "~/reports") == ([self.MOUNTS[0]], "reports")
        assert narrow(self.MOUNTS, "@team/reports") == ([self.MOUNTS[1]], "reports")

    def test_bare_prefix_selects_workspace_root(self) -> None:
        assert narrow(self.MOUNTS, "~") == ([self.MOUNTS[0]], None)
        assert narrow(self.MOUNTS, "@team") == ([self.MOUNTS[1]], None)

    def test_unprefixed_value_spans_every_workspace(self) -> None:
        assert narrow(self.MOUNTS, "reports") == (self.MOUNTS, "reports")
        assert narrow(self.MOUNTS, None) == (self.MOUNTS, None)

    def test_unknown_prefix_falls_through(self) -> None:
        assert narrow(self.MOUNTS, "@ghost/x") == (self.MOUNTS, "@ghost/x")


class TestPathCanonicalization:
    """A filter must see the file an operation touches, not the alias it named."""

    @staticmethod
    def _scoped(root: Path) -> SearchPath:
        """A personal-workspace root that hides ``excluded.md``."""
        return SearchPath.of(
            path=root,
            target="~",
            implied=True,
            filter_func=DocumentFilter(excluded=frozenset({doc("excluded.md")})),
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
        tool = list_documents(mounts_of((self._scoped(tmp_path),)))

        with pytest.raises(Retry, match="does not exist"):
            await tool(path="~/sub/..", patterns=["*.md"])

    async def test_listing_subdirectory_cannot_escape_workspace(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret")
        tool = list_documents(
            mounts_of((SearchPath.of(path=workspace, target="~", implied=True),))
        )

        with pytest.raises(Retry, match="does not exist"):
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
        tool = list_documents(
            mounts_of((SearchPath.of(path=workspace, target="~", implied=True),))
        )

        with pytest.raises(Retry, match="does not exist"):
            await tool(path="~/alias", max_depth=None)


class TestListDocuments:
    """Tests for list_documents (listing and pattern matching)."""

    async def test_empty_dir(self, tmp_path: Path) -> None:
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        assert (await tool()).metadata == []

    @pytest.mark.parametrize("path", [".", "./", "~/."])
    async def test_root_directory_aliases(self, tmp_path: Path, path: str) -> None:
        (tmp_path / "a.md").write_text("hello")
        paths = (SearchPath.of(path=tmp_path, target="~", implied=True),)

        data = (await list_documents(mounts_of(paths))(path=path)).metadata
        matches = (
            await list_documents(mounts_of(paths))(path=path, patterns=["*.md"])
        ).metadata

        assert [entry.file_path for entry in data] == ["~/a.md"]
        assert [entry.file_path for entry in matches] == ["~/a.md"]

    async def test_missing_subdirectory_is_retry(self, tmp_path: Path) -> None:
        tool = list_documents(
            mounts_of((SearchPath.of(path=tmp_path, target="~", implied=True),))
        )

        with pytest.raises(Retry, match="Directory '~/missing' does not exist"):
            await tool(path="~/missing")

    async def test_subdirectory_traversal_is_folded(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("hello")
        tool = list_documents(
            mounts_of((SearchPath.of(path=tmp_path, target="~", implied=True),))
        )

        data = (await tool(path="~/sub/../docs")).metadata

        assert [entry.file_path for entry in data] == ["~/docs/a.md"]

    async def test_unknown_prefix_names_the_roots(self, tmp_path: Path) -> None:
        tool = list_documents(
            mounts_of((SearchPath.of(path=tmp_path, target="~", implied=True),))
        )

        with pytest.raises(Retry, match="This tool addresses ~"):
            await tool(path="@nope/x")

    async def test_empty_listing_names_its_scope(self, tmp_path: Path) -> None:
        (tmp_path / "doc.assets").mkdir()
        (tmp_path / "doc.assets" / "img.png").write_bytes(b"\x89PNG")
        tool = list_documents(
            mounts_of((SearchPath.of(path=tmp_path, target="~", implied=True),))
        )

        empty = await tool(path="~/doc.assets", max_depth=None)

        assert empty.metadata == []
        assert empty.return_value == (
            "(no documents under '~/doc.assets')\n\n[1 hidden entry (`.assets` "
            "contents and common build/vendor directories), pass "
            "include_ignored=True to reveal them]"
        )

    async def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool(path="notes")).metadata
        filenames = [r.file_path for r in data]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    async def test_lists_all_file_types(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")
        (tmp_path / "c.png").write_bytes(b"\x89PNG")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool()).metadata
        filenames = {r.file_path for r in data}
        assert filenames == {"a.md", "b.txt", "c.png"}

    async def test_nonexistent_dir(self, tmp_path: Path) -> None:
        tool = list_documents(mounts_of((SearchPath.of(tmp_path / "nonexistent"),)))
        assert (await tool()).metadata == []

    async def test_assets_contents_hidden_unless_ignored_included(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "doc.md").write_text("text")
        assets = tmp_path / "doc.assets"
        assets.mkdir()
        (assets / "img.png").write_bytes(b"\x89PNG")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        filenames = {r.file_path for r in (await tool(max_depth=None)).metadata}
        assert filenames == {"doc.md", "doc.assets"}
        revealed = {
            r.file_path
            for r in (await tool(max_depth=None, include_ignored=True)).metadata
        }
        assert "doc.assets/img.png" in revealed

    async def test_multi_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = list_documents(
            mounts_of(
                (
                    SearchPath.of(path=user_dir),
                    SearchPath.of(path=group_dir, target="@team"),
                )
            )
        )
        data = (await tool()).metadata
        filenames = {r.file_path for r in data}
        assert filenames == {"a.md", "@team/b.md"}

    async def test_prefix_scopes_to_one_store(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "a.md").write_text("user")
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "b.md").write_text("group")
        tool = list_documents(
            mounts_of(
                (
                    SearchPath.of(path=user_dir, target="~", implied=True),
                    SearchPath.of(path=group_dir, target="@team"),
                )
            )
        )
        filenames = {r.file_path for r in (await tool(path="@team")).metadata}
        assert filenames == {"@team/b.md"}

    async def test_includes_directories(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool(max_depth=None)).metadata
        dirs = [r for r in data if r.is_directory]
        assert any(r.file_path == "notes" for r in dirs)

    async def test_max_depth_default_excludes_nested(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool()).metadata
        filenames = {r.file_path for r in data}
        assert "top.md" in filenames
        assert "notes" in filenames
        assert "notes/n.md" not in filenames

    async def test_max_results_limits_list(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool(max_results=3)).metadata
        assert isinstance(data, list)
        assert len(data) == 3

    async def test_skips_build_dirs_by_default(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"x")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool(max_depth=None)).metadata
        filenames = {r.file_path for r in data}
        assert "src.py" in filenames
        assert "__pycache__" not in filenames
        assert "__pycache__/junk.pyc" not in filenames

    async def test_include_ignored_exposes_build_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("x")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"x")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        data = (await tool(max_depth=None, include_ignored=True)).metadata
        filenames = {r.file_path for r in data}
        assert "__pycache__" in filenames

    async def test_empty_result_hint_counts_hidden_entries(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "a.pyc").write_bytes(b"x")
        (cache / "b.pyc").write_bytes(b"x")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))
        result = await tool(max_depth=None)
        assert result.metadata == []
        assert "3 hidden entries" in result.return_value
        assert "include_ignored=True" in result.return_value
        assert (
            await tool(max_depth=None, include_ignored=True)
        ).return_value != result.return_value

    async def test_a_listing_names_what_it_left_out(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("x")
        (tmp_path / "doc.assets").mkdir()
        (tmp_path / "doc.assets" / "a.png").write_bytes(b"x")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))

        hidden = (await tool(max_depth=3)).return_value
        deep = (await tool(include_ignored=True)).return_value
        capped = (await tool(max_results=1)).return_value

        assert "1 hidden entry" in hidden
        assert "1 entry below max_depth=1" in deep
        assert "reached max_results=1" in capped

    async def test_patterns_match_files_at_any_depth(self, tmp_path: Path) -> None:
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "deep.md").write_text("a")
        (tmp_path / "readme.md").write_text("b")
        (tmp_path / "data.txt").write_text("c")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))

        data = (await tool(patterns=["*.md"])).metadata

        assert [r.file_path for r in data] == ["notes/deep.md", "readme.md"]
        assert not any(r.is_directory for r in data)

    async def test_patterns_are_unioned_and_each_file_listed_once(
        self, tmp_path: Path
    ) -> None:
        for name in ("a.md", "b.txt", "c.csv"):
            (tmp_path / name).write_text(name)

        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))

        data = (await tool(patterns=["*.md", "*.txt", "a.*"])).metadata

        assert [r.file_path for r in data] == ["a.md", "b.txt"]

    async def test_patterns_respect_subdir_scoping(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "a.md").write_text("a")
        (tmp_path / "top.md").write_text("top")
        tool = list_documents(mounts_of((SearchPath.of(tmp_path),)))

        result = await tool(path="notes", patterns=["*.md"])
        empty = await tool(patterns=["*.csv"])

        assert [r.file_path for r in result.metadata] == ["notes/a.md"]
        assert empty.return_value == "(no matches in the workspace)"


class TestReadDocument:
    """Tests for read_document (line-range reads with line numbers)."""

    async def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("content here")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        result = single((await tool([DocumentRead("doc.md")])).metadata)
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
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        result = single((await tool([DocumentRead("SU\u0308VOA.md")])).metadata)
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
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="report.md"):
            await tool([DocumentRead("report.docx")])

    async def test_projection_of_a_table_names_query_table(
        self, tmp_path: Path
    ) -> None:
        # The read that matters: an uploaded spreadsheet is only ever addressed
        # by its markdown projection, so keying the hint on the requested
        # suffix left query_table invisible for exactly the file it exists for.
        (tmp_path / "lab.xlsx").write_bytes(b"PK\x03\x04\xec\xec binary")
        (tmp_path / "lab.md").write_text("| a | b |\n|---|---|\n| 1 | 2 |")
        tool = read_document(
            mounts_of((SearchPath.of(path=tmp_path, target="~", implied=True),))
        )

        result = await tool([DocumentRead("~/lab.md")])
        assert "query_table" in result.return_value
        assert "'~/lab.xlsx'" in result.return_value

    async def test_plain_markdown_names_no_query_tool(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("prose")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))

        result = await tool([DocumentRead("notes.md")])
        assert "query_table" not in result.return_value

    async def test_binary_table_is_refused_naming_query_table(
        self, tmp_path: Path
    ) -> None:
        # Both halves of the seam: the tool that answers without reading leads,
        # and the extracted text stays named behind it.
        (tmp_path / "lab.xlsx").write_bytes(b"PK\x03\x04\xec\xec binary")
        (tmp_path / "lab.md").write_text("extracted text")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))

        with pytest.raises(Retry, match=r"query_table.*'lab\.md'"):
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
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="read_binary_document"):
            await tool([DocumentRead(f"scan{suffix}")])

    async def test_binary_tool_points_unshowable_input_at_its_sidecar(
        self, tmp_path: Path
    ) -> None:
        # An Office document is neither showable nor readable as text, so the
        # refusal names the extracted text rather than the tool that would
        # refuse it in turn.
        (tmp_path / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        tool = read_binary_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="report.md"):
            await tool([BinaryRead("report.docx")])

    async def test_binary_tool_preserves_scope_in_sidecar_hint(
        self, tmp_path: Path
    ) -> None:
        group_dir = tmp_path / "team"
        group_dir.mkdir()
        (group_dir / "report.docx").write_bytes(b"PK\x03\x04\xec\xec binary")
        tool = read_binary_document(
            mounts_of((SearchPath.of(path=group_dir, target="@team"),))
        )

        with pytest.raises(Retry, match=r"@team/report\.md"):
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

        def render(
            raw: bytes, spec: str | None, /, *, max_dimension: int, max_pages: int
        ) -> tuple[PageImage, ...]:
            seen.append(max_pages)
            raise ValueError(
                f"3 pages exceeds the {max_pages}-page limit, narrow with pages="
            )

        monkeypatch.setattr("exar.tools._media.render_pages", render)
        tool = read_binary_document(
            mounts_of((SearchPath.of(tmp_path),)), max_images=2, executor=None
        )

        with pytest.raises(Retry, match="narrow with pages="):
            await tool([BinaryRead("report.pdf")])

        assert seen == [2]

        # Two files share the cap, so each is bounded by its share of it.
        (tmp_path / "other.pdf").write_bytes(b"%PDF-1.4\n")
        seen.clear()
        tool = read_binary_document(
            mounts_of((SearchPath.of(tmp_path),)), max_images=3, executor=None
        )

        with pytest.raises(Retry, match="Every item failed"):
            await tool([BinaryRead("report.pdf"), BinaryRead("other.pdf")])

        assert sorted(seen) == [1, 2]

    async def test_native_pdfs_do_not_spend_the_image_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("a.pdf", "b.pdf", "c.gif"):
            (tmp_path / name).write_bytes(b"content")

        seen: list[int] = []

        def sample(_data: bytes, /, *, max_frames: int, max_dimension: int) -> Sample:
            seen.append(max_frames)

            return Sample(frames=(), duration=0.0)

        monkeypatch.setattr("exar.tools._media.sample_animation", sample)
        tool = read_binary_document(
            mounts_of((SearchPath.of(tmp_path),)),
            executor=None,
            binary_content_mode=BinaryContentMode.NATIVE,
            max_images=1,
        )

        result = await tool(
            [BinaryRead("a.pdf"), BinaryRead("b.pdf"), BinaryRead("c.gif")]
        )

        files = unpack(result).files
        assert [a.media_type for a in files if isinstance(a, BinaryContent)] == [
            "application/pdf"
        ] * 2
        assert seen == [1]

    async def test_binary_without_companion_retries(self, tmp_path: Path) -> None:
        # Undecodable bytes become a recoverable Retry, never a run-aborting
        # UnicodeDecodeError.
        (tmp_path / "blob.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xec\xec\xff\xfe")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="not text"):
            await tool([DocumentRead("blob.bin")])

    async def test_legacy_encoding_is_decoded_and_reported(
        self, tmp_path: Path
    ) -> None:
        # A cp1252/UTF-16 original is content, not a binary: it is decoded
        # rather than refused, and the source encoding is named so a wrong
        # guess on short input is visible instead of silent.
        (tmp_path / "settings.ini").write_bytes("Benutzer = Jörg\n".encode("utf-16"))
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))

        result = await tool([DocumentRead("settings.ini")])

        assert isinstance(single(result.metadata), DocumentRange)
        assert single(result.metadata).content == "Benutzer = Jörg"
        assert "decoded from utf-16" in result.return_value

    async def test_rejects_nonexistent(self, tmp_path: Path) -> None:
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="not found"):
            await tool([DocumentRead("missing.md")])

    async def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="not found"):
            await tool([DocumentRead("../../../etc/passwd")])

    async def test_rejects_a_directory_as_a_directory(self, tmp_path: Path) -> None:
        # A selected folder reaches the model as a path like any other, so it
        # is read like one. "Not found" sent that run hunting for a respelling
        # of a path that was right; the listing tool is the actual correction.
        (tmp_path / "reports").mkdir()
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))

        with pytest.raises(Retry, match="is a directory.*list_documents"):
            await tool([DocumentRead("reports/")])

    async def test_reads_group_document(self, tmp_path: Path) -> None:
        group_dir = tmp_path / "group"
        group_dir.mkdir()
        (group_dir / "doc.md").write_text("group content")
        tool = read_document(
            mounts_of(
                (
                    SearchPath.of(path=tmp_path),
                    SearchPath.of(path=group_dir, target="@team"),
                )
            )
        )
        result = single((await tool([DocumentRead("@team/doc.md")])).metadata)
        assert isinstance(result, DocumentRange)
        assert result.content == "group content"

    async def test_rejects_unknown_prefix(self, tmp_path: Path) -> None:
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        with pytest.raises(Retry, match="not found"):
            await tool([DocumentRead("@unknown/doc.md")])

    async def test_formatted_always_includes_line_numbers(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("alpha\nbeta")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        formatted = (await tool([DocumentRead("doc.md")])).return_value
        assert "1: alpha" in formatted
        assert "2: beta" in formatted

    async def test_char_cap_truncates_within_window(self, tmp_path: Path) -> None:
        content = "x" * 200
        (tmp_path / "big.md").write_text(content)
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)), max_chars=50)
        result = single((await tool([DocumentRead("big.md")])).metadata)
        assert isinstance(result, DocumentRange)
        # Single 200-char line fits in the window so it's kept whole;
        # but a longer file with multiple lines would get clipped.
        assert len(result.content) == 200

    async def test_char_cap_clips_multiline(self, tmp_path: Path) -> None:
        lines = ["y" * 50 for _ in range(10)]
        (tmp_path / "big.md").write_text("\n".join(lines))
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)), max_chars=120)
        result = single((await tool([DocumentRead("big.md")])).metadata)
        assert isinstance(result, DocumentRange)
        # 120-char budget fits ~2 lines (each 50 + newline = 51 chars).
        assert result.end_line < result.total_lines

    async def test_long_line_truncated_in_formatted_only(self, tmp_path: Path) -> None:
        # A base64-image line the window returns whole (it is the first
        # selected line) must not flood the model context: the formatted
        # output truncates it, while the structured content keeps it intact.
        long_line = "data:image/png;base64," + "A" * 500_000
        (tmp_path / "img.md").write_text(f"{long_line}\ntail")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)), max_line_chars=80)
        out = await tool([DocumentRead("img.md")])
        assert "…" in out.return_value
        assert len(max(out.return_value.splitlines(), key=len)) < 200
        assert isinstance(single(out.metadata), DocumentRange)
        assert long_line in single(out.metadata).content

    async def test_tabular_file_is_pointed_at_query_table(self, tmp_path: Path) -> None:
        # The one moment the caller finds out a line read was the wrong tool
        # for this file is when it reads one, so the read says so.
        (tmp_path / "sales.csv").write_text("region,amount\nEU,100")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        formatted = (await tool([DocumentRead("sales.csv")])).return_value
        assert "query_table" in formatted

    async def test_full_lines_opts_out_of_the_per_line_clip(
        self, tmp_path: Path
    ) -> None:
        # A wide markdown table row loses its trailing columns to the clip with
        # nothing but an ellipsis to show for it, which the reader cannot spot
        # from the output alone: the clip is named, and full_lines undoes it.
        row = "| " + " | ".join(f"col{i}" for i in range(200)) + " |"
        (tmp_path / "table.md").write_text(row)
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)), max_line_chars=80)

        clipped = (await tool([DocumentRead("table.md")])).return_value
        assert "full_lines=true" in clipped
        assert "col199" not in clipped

        whole = (await tool([DocumentRead("table.md")], full_lines=True)).return_value
        assert "col199" in whole
        assert "full_lines=true" not in whole

    async def test_reads_several_documents_each_under_its_own_key(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("\n".join(f"a{i}" for i in range(10)))
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))

        out = await tool(
            [DocumentRead("a.md", offset=3, limit=2), DocumentRead("b.md")]
        )

        first, second = out.metadata
        assert isinstance(first, DocumentRange)
        assert (first.file_path, first.content) == ("a.md", "a2\na3")
        assert second == ItemFailure(item="b.md", reason="'b.md' not found.")
        assert "==> a.md (offset=3, limit=2) <==" in out.return_value
        assert "offset=5" in out.return_value

    # --- offset / limit tests ---

    async def test_correct_range(self, tmp_path: Path) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5"]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        result = single(
            (await tool([DocumentRead("doc.md", offset=2, limit=3)])).metadata
        )
        assert isinstance(result, DocumentRange)
        assert result.start_line == 2
        assert result.end_line == 4
        assert result.total_lines == 5
        assert result.content == "line2\nline3\nline4"

    async def test_defaults_to_full_file_when_small(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        result = single((await tool([DocumentRead("doc.md")])).metadata)
        assert isinstance(result, DocumentRange)
        assert result.start_line == 1
        assert result.end_line == 3
        assert result.content == "a\nb\nc"

    async def test_offset_without_limit(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)))
        result = single((await tool([DocumentRead("doc.md", offset=2)])).metadata)
        assert isinstance(result, DocumentRange)
        assert result.start_line == 2
        assert result.end_line == 3
        assert result.content == "b\nc"

    async def test_default_window_is_the_char_budget(self, tmp_path: Path) -> None:
        # Five lines rendered as `1: line0`, eight characters and a newline
        # each, fit 50 characters, the numbers counted with the text.
        lines = [f"line{i}" for i in range(10)] * 10
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = read_document(mounts_of((SearchPath.of(tmp_path),)), max_chars=50)
        output = await tool([DocumentRead("doc.md")])
        result = single(output.metadata)
        assert isinstance(result, DocumentRange)
        assert result.end_line == 5
        assert result.total_lines == 100
        assert "95 more lines, call again with offset=6" in output.return_value


class TestServeBatch:
    """The list-first contract every batched tool is built on."""

    @staticmethod
    async def _serve(item: str, share: BatchShare) -> ToolReturn[str]:
        """Refuse a `bad` item, and finish the first item last."""
        await asyncio.sleep(0.01 if share.index == 0 else 0)

        if item.startswith("bad"):
            raise Retry(f"{item} refused")

        return tool_return(f"{item}:{share.of(10)}")

    async def test_dedupes_and_keeps_request_order_with_split_budgets(self) -> None:
        out = await serve_batch(
            ["c", "a", "c", "b"], self._serve, key=str, concurrency=3
        )

        assert out.metadata == ("c:4", "a:3", "b:3")
        assert out.return_value == "==> c <==\nc:4\n\n==> a <==\na:3\n\n==> b <==\nb:3"

    async def test_a_failed_item_is_reported_in_place(self) -> None:
        out = await serve_batch(["bad", "a"], self._serve, key=str)

        assert out.metadata == (ItemFailure(item="bad", reason="bad refused"), "a:5")
        assert "==> bad <==\nfailed: bad refused" in unpack(out).text
        assert TypeAdapter(BatchData[str]).dump_python(out.metadata, mode="json") == [
            {"item": "bad", "reason": "bad refused", "kind": "failure"},
            "a:5",
        ]

    async def test_only_a_batch_with_every_item_failed_is_refused(self) -> None:
        with pytest.raises(Retry, match="^bad: bad refused$"):
            await serve_batch(["bad"], self._serve, key=str)

        with pytest.raises(Retry, match="Every item failed:\n- bad1: bad1"):
            await serve_batch(["bad1", "bad2"], self._serve, key=str)


class TestEditDocument:
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
        tool = edit_document((SearchPath.of(tmp_path),), commit=_commit)
        result = (await tool("doc.md", edits, expected_hash="h")).metadata
        assert result == "edited"
        assert calls == [Changeset((Edit("doc.md", tuple(edits), "h"),))]

    async def test_rejects_inaccessible_path(self, tmp_path: Path) -> None:
        tool = edit_document((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="not accessible"):
            await tool("../escape.md", [TextEdit("a", "b")])

    async def test_translates_a_refused_commit(self, tmp_path: Path) -> None:
        async def _commit(*_: object) -> str:
            raise ChangeRefused(status_code=404, detail="Document not found")

        tool = edit_document((SearchPath.of(tmp_path),), commit=_commit)

        with pytest.raises(Retry, match="Document not found"):
            await tool("doc.md", [TextEdit("a", "b")])


async def _echo_write(changeset: Changeset[str]) -> str:
    """A commit that names the document it was given."""
    return f"wrote {changeset.locations[0]}"


class TestWriteDocument:
    """The tool resolves and access-checks the path, then commits one write.

    The write algorithm itself lives in the changeset gateway and is
    covered by ``TestWriteDocumentText``.
    """

    async def test_commits_one_write(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _commit(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "written"

        tool = write_document((SearchPath.of(tmp_path),), commit=_commit)
        result = (
            await tool("doc.md", "content", mode="append", expected_hash="h")
        ).metadata
        assert result == "written"
        assert calls == [Changeset((Write("doc.md", "content", "append", "h"),))]

    async def test_a_row_of_the_wrong_width_is_refused(self, tmp_path: Path) -> None:
        # A summary row written with the separators counted by hand puts its
        # value under the wrong heading, which nothing downstream reports.
        tool = write_document((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="line 3 has 2 fields"):
            await tool("t.csv", "date,load,ew\n2023-01-01,1,2\n,mean: 3\n")

    async def test_an_even_table_is_written(self, tmp_path: Path) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)

        result = await tool("t.csv", "date,load,ew\n2023-01-01,1,2\n,,3\n")

        assert result.metadata

    async def test_the_separator_comes_from_the_suffix(self, tmp_path: Path) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="line 2 has 4 fields"):
            await tool("t.tsv", "a\tb\tc\n1\t2\t3\t4\n")

    async def test_a_semicolon_file_named_csv_is_one_column(
        self, tmp_path: Path
    ) -> None:
        # A `.csv` is comma-separated by the name it was given, so this is one
        # column with no width to disagree with, and query_table reads it back
        # the same way, which is the point of sharing one separator table.
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)

        assert (await tool("t.csv", "a;b;c\n1;2;3;4\n")).metadata

    async def test_a_languages_word_for_missing_is_not_a_cell(
        self, tmp_path: Path
    ) -> None:
        # An f-string handed the value rather than the text for it, and the gap
        # reads downstream as the four-letter word instead.
        tool = write_document((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="'None'"):
            await tool("t.csv", "date,load\n2023-01-01,1\n2023-01-02,None\n")

    async def test_a_lab_sentinel_is_left_alone(self, tmp_path: Path) -> None:
        # `N/A` is written on purpose; `None` never is.
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)

        assert (await tool("t.csv", "date,load\n2023-01-01,N/A\n")).metadata

    async def test_prose_is_not_a_table(self, tmp_path: Path) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)

        assert (await tool("notes.md", "a line\nand, another\n")).metadata

    async def test_any_text_document_is_written(self, tmp_path: Path) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)
        result = (await tool("data.txt", "content")).metadata
        assert result == "wrote data.txt"

    async def test_translates_a_refused_commit(self, tmp_path: Path) -> None:
        async def _mutate(*_: object) -> str:
            raise ChangeRefused(status_code=400, detail="Unsupported write mode: x")

        tool = write_document((SearchPath.of(tmp_path),), commit=_mutate)

        with pytest.raises(Retry, match="Unsupported write mode"):
            await tool("doc.md", "content")

    async def test_binary_format_is_refused_before_the_commit_runs(
        self, tmp_path: Path
    ) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="binary format"):
            await tool("sheet.xlsx", "a,b")

    async def test_text_formats_a_converter_claims_are_writable(
        self, tmp_path: Path
    ) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)

        for name in ("rows.csv", "page.html", "diagram.svg"):
            assert (await tool(name, "x")).metadata == f"wrote {name}"


class TestMoveDocuments:
    """The tool resolves both ends of every move, then delegates the batch."""

    async def test_delegates_every_move_at_once(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _move(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "moved"

        (tmp_path / "old.md").write_text("x")
        (tmp_path / "images").mkdir()
        tool = move_documents((SearchPath.of(tmp_path),), commit=_move)
        result = await tool(
            [DocumentMove("old.md", "notes/new.md"), DocumentMove("images", "notes")]
        )

        assert calls == [
            Changeset((Move("old.md", "notes/new.md"), Move("images", "notes")))
        ]
        assert result.metadata == "moved"

    async def test_a_missing_source_refuses_the_whole_batch(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "old.md").write_text("x")
        tool = move_documents((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="'gone.md' not found"):
            await tool(
                [DocumentMove("old.md", "new.md"), DocumentMove("gone.md", "b.md")]
            )

    async def test_translates_a_refused_commit(self, tmp_path: Path) -> None:
        async def _move(*_: object) -> str:
            raise ChangeRefused(status_code=409, detail="Destination already exists")

        (tmp_path / "old.md").write_text("x")
        tool = move_documents((SearchPath.of(tmp_path),), commit=_move)

        with pytest.raises(Retry, match="Destination already exists"):
            await tool([DocumentMove("old.md", "new.md")])


@pytest.mark.parametrize("excluded", [doc("docs/deep/secret.md"), folder("docs/deep")])
async def test_a_directory_move_cannot_carry_excluded_entries(
    data_dir: Path, excluded: FilterEntry
) -> None:
    store = HostGrant().workspace(UserScope("u"))
    secret = store.path / "docs" / "deep" / "secret.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret")
    hidden = DocumentFilter(excluded=frozenset({excluded}))
    gateway = Gateway((Root.of(store, hidden),))

    with pytest.raises(ChangeRefused, match="contains inaccessible entries") as error:
        _ = await gateway.plan(Changeset((Move("~/docs", "~/archive"),)))

    assert "secret" not in str(error.value.detail)


class TestDeleteDocuments:
    """The tool resolves every existing path, then delegates the batch."""

    async def test_delegates_every_path_at_once(self, tmp_path: Path) -> None:
        calls: list[Changeset[str]] = []

        async def _delete(changeset: Changeset[str]) -> str:
            calls.append(changeset)

            return "deleted"

        (tmp_path / "doc.md").write_text("x")
        (tmp_path / "state.json").write_text("{}")
        tool = delete_documents((SearchPath.of(tmp_path),), commit=_delete)

        assert (await tool(["doc.md", "state.json"])).metadata == "deleted"
        assert calls == [Changeset((Delete("doc.md"), Delete("state.json")))]

    async def test_refuses_a_directory_and_a_missing_document(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "notes").mkdir()
        tool = delete_documents((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match="is a directory"):
            await tool(["notes"])

        with pytest.raises(Retry, match="not found"):
            await tool(["gone.md"])


async def _unreachable(*_: object) -> Any:
    """Every commit a path-rejection test hands its tool: it must not run."""
    raise AssertionError("commit must not run when the path is rejected")


class TestJq:
    """Tests for jq."""

    @staticmethod
    def _result(output: BatchData[JqResult]) -> JqResult:
        return single(output)

    async def test_filter_selects_values(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"title": "Hello", "n": 42}))
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        result = self._result((await tool(["item.json"], ".title")).metadata)
        assert result.values == ("Hello",)

    async def test_missing_filter_reports_the_shape(self, tmp_path: Path) -> None:
        """The cheap first call: keys and types, never the document itself."""
        (tmp_path / "item.json").write_text(json.dumps({"title": "Hello", "n": 42}))
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        output = await tool(["item.json"])
        result = self._result(output.metadata)
        assert result.values == ({"title": "string", "n": "number"},)
        assert "Hello" not in output.return_value

    async def test_non_json_document_is_turned_away(self, tmp_path: Path) -> None:
        """The suffix table decides, so the reader and the filter cannot disagree."""
        (tmp_path / "notes.md").write_text("# notes")
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        with pytest.raises(Retry, match="not a JSON document"):
            await tool(["notes.md"], ".")

    async def test_invalid_jq_expression(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"x": 1}))
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        with pytest.raises(Retry):
            await tool(["item.json"], "invalid [[[")

    async def test_malformed_document_is_correctable(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text("{not json")
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        with pytest.raises(Retry, match="jq failed"):
            await tool(["item.json"], ".")

    async def test_nonexistent_file_path(self, tmp_path: Path) -> None:
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        with pytest.raises(Retry, match="not found"):
            await tool(["missing.json"], ".")

    async def test_path_traversal(self, tmp_path: Path) -> None:
        tool = jq(mounts_of((SearchPath.of(path=tmp_path),)))
        with pytest.raises(Retry, match="not found"):
            await tool(["../etc/passwd"], ".")


class TestGrepSearch:
    """Tests for grep against real files on disk."""

    @staticmethod
    def _filenames(output: list[GrepMatch]) -> set[str]:
        return {m.file_path for m in output}

    async def test_legacy_encoded_sibling_keeps_other_results(
        self, tmp_path: Path
    ) -> None:
        # A Latin-1 line reaches the JSON output base64-encoded rather than as
        # text; mis-parsing it used to discard every match of the whole root.
        (tmp_path / "legacy.xml").write_bytes(
            "<a name='Leitfähigkeit' unit='°C' />\n".encode("cp1252")
        )
        (tmp_path / "modern.xml").write_text("<a name='utf8' />\n")
        result = await grep(mounts_of((SearchPath.of(tmp_path),)))("name=")
        assert self._filenames(result.metadata) == {"legacy.xml", "modern.xml"}
        assert "Leitfähigkeit" in result.return_value

    async def test_a_glob_naming_a_binary_format_is_refused(
        self, tmp_path: Path
    ) -> None:
        # ripgrep skips a binary file without a word, so this search answers
        # "(no matches)", a clean negative that is really a file never opened,
        # and one a run reasonably takes for proof that the term is absent.
        with pytest.raises(Retry, match="query_table"):
            await grep(mounts_of((SearchPath.of(tmp_path),)))("TKN", glob="*.xlsx")

    async def test_a_text_glob_still_searches(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("TKN appears here\n")

        result = await grep(mounts_of((SearchPath.of(tmp_path),)))("TKN", glob="*.md")

        assert self._filenames(result.metadata) == {"notes.md"}

    @pytest.mark.parametrize("glob", [None, "*.{xml,md}"])
    async def test_original_dropped_when_description_matches(
        self, tmp_path: Path, glob: str | None
    ) -> None:
        (tmp_path / "doc.xml").write_text("<a>needle</a>\n")
        (tmp_path / "doc.md").write_text("```xml\n<a>needle</a>\n```\n")
        (tmp_path / "raw.xml").write_text("<a>needle</a>\n")
        result = await grep(mounts_of((SearchPath.of(tmp_path),)))("needle", glob=glob)
        assert self._filenames(result.metadata) == {"doc.md", "raw.xml"}

    async def test_original_kept_when_globbed(self, tmp_path: Path) -> None:
        (tmp_path / "doc.xml").write_text("<a>needle</a>\n")
        (tmp_path / "doc.md").write_text("```xml\n<a>needle</a>\n```\n")
        result = await grep(mounts_of((SearchPath.of(tmp_path),)))(
            "needle", glob="*.xml"
        )
        assert self._filenames(result.metadata) == {"doc.xml"}

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
            SearchPath.of(path=tmp_path / "user", target="~", implied=True),
            SearchPath.of(path=tmp_path / "group", target="@team"),
        )

        both = await grep(mounts_of(paths))("needle", glob="reports/*.md")
        one = await grep(mounts_of(paths))("needle", glob="~/reports/*.md")

        assert self._filenames(both.metadata) == {
            "~/reports/q1.md",
            "@team/reports/q1.md",
        }
        assert self._filenames(one.metadata) == {"~/reports/q1.md"}

    async def test_assets_payload_hidden_unless_ignored(self, tmp_path: Path) -> None:
        assets = tmp_path / "doc.assets"
        assets.mkdir()
        (assets / "fig1.txt").write_text("needle\n")
        hidden = await grep(mounts_of((SearchPath.of(tmp_path),)))("needle")
        assert hidden.metadata == []
        assert "1 hidden entry" in hidden.return_value
        revealed = await grep(mounts_of((SearchPath.of(tmp_path),)))(
            "needle", include_ignored=True
        )
        assert self._filenames(revealed.metadata) == {"doc.assets/fig1.txt"}

    async def test_a_capped_result_says_how_many_it_left_out(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.md").write_text("needle\n")
        (tmp_path / "b.md").write_text("needle\n")

        result = await grep(mounts_of((SearchPath.of(tmp_path),)))(
            "needle", max_results=1, output_mode="files_with_matches"
        )

        assert "showing 1 of 2" in result.return_value
        assert self._filenames(result.metadata) == {"a.md"}

    async def test_a_capped_search_cuts_at_the_cap_and_names_the_total(
        self, tmp_path: Path
    ) -> None:
        # The block that reaches past the cap is cut after its last shown
        # match, and a file past it is never read for its lines.
        (tmp_path / "a.md").write_text("needle\n" * 3)
        (tmp_path / "b.md").write_text("needle\n" * 100)
        (tmp_path / "c.md").write_text("needle\n")

        result = await grep(mounts_of((SearchPath.of(tmp_path),)))(
            "needle", max_results=5
        )

        assert [sum(line.is_match for line in m.lines) for m in result.metadata] == [
            3,
            2,
        ]
        assert "showing 5 of 104" in result.return_value

    async def test_an_invalid_pattern_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("needle(\n")

        with pytest.raises(Retry, match="regex parse error"):
            await grep(mounts_of((SearchPath.of(tmp_path),)))("needle(")

    async def test_parent_glob_cannot_search_outside_workspace(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "inside.md").write_text("inside\n")
        (tmp_path / "secret.md").write_text("needle\n")
        tool = grep(
            mounts_of((SearchPath.of(path=workspace, target="~", implied=True),))
        )

        result = await tool("needle", glob="../*.md")

        assert result.metadata == []


class TestGrepFormatting:
    """Tests for grep match formatting and the line-level char budget."""

    @staticmethod
    def _block(n: int, filename: str = "f.md") -> GrepMatch:
        return GrepMatch(
            file_path=filename,
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
        block = GrepMatch(
            file_path="doc.md",
            lines=(
                GrepLine(line_number=10, text="ctx", is_match=False),
                GrepLine(line_number=11, text="hit", is_match=True),
            ),
        )
        assert _format_matches([block], LineClip(None)) == "doc.md\n10-ctx\n11:hit"

    def test_a_wide_line_is_clipped_around_its_match(self, tmp_path: Path) -> None:
        # A hit deep in a wide table row survives the clip, and full_lines
        # lifts it from the context row and the match alike.
        block = GrepMatch(
            file_path="doc.md",
            lines=(
                GrepLine(line_number=1, text="| h " * 50, is_match=False),
                GrepLine(
                    line_number=2,
                    text="x" * 100 + "Legende" + "y" * 100,
                    is_match=True,
                    match_start=100,
                ),
            ),
        )

        clip = LineClip(20)
        clipped = _format_matches([block], clip).splitlines()
        whole = _format_matches([block], LineClip(None)).splitlines()

        assert clipped[1] == f"1-{block.lines[0].text[:19]}…"
        assert clipped[2] == "2:…xxxxLegendeyyyyyyy…"
        assert whole[1] == f"1-{block.lines[0].text}"
        assert whole[2] == f"2:{block.lines[1].text}"
        assert clip.hints()

    def test_blocks_within_document_separated_by_dashes(self, tmp_path: Path) -> None:
        formatted = _format_matches([self._block(1), self._block(1)], LineClip(None))
        assert formatted == "f.md\n1:x\n--\n1:x"

    async def test_separate_documents_joined_by_separator(self, tmp_path: Path) -> None:
        formatted = _format_matches(
            [self._block(1, "a.md"), self._block(1, "b.md")], LineClip(None)
        )
        assert formatted == "a.md\n1:x\n---\nb.md\n1:x"


type PythonFactory = Callable[..., ToolFunc]
"""`run_python` with the pool and limits of the test bound."""


def _recording_python_tool(
    python: PythonFactory, workspace: Path
) -> tuple[ToolFunc, list[Changeset[str]]]:
    """Mount *workspace* writable and return what each run stages."""
    staged: list[Changeset[str]] = []

    async def commit(changes: Changeset[str]) -> ChangesetOutcome:
        staged.append(changes)

        return PendingChanges("staged-id", ChangesetSummary(), 0)

    scoped = SearchPath.of(path=workspace, target="~", implied=True)
    configured = python((scoped,), writable=(scoped,), commit=commit)

    return configured, staged


class TestRunPython:
    """Tests for run_python."""

    @pytest.fixture()
    async def python(self) -> AsyncIterator[PythonFactory]:
        async with AsyncMonty(min_processes=1) as pool:
            yield partial(run_python, pool=lambda: pool, changeset_limits=LIMITS)

    async def test_returns_value_and_printed_output(
        self, python: PythonFactory
    ) -> None:
        result = await python()("import math\nprint('working')\nmath.factorial(5)")
        assert result.metadata == PythonResult(result="120", stdout="working")
        assert result.return_value == "working\nResult: 120"

    async def test_an_elided_result_names_the_cut(self, python: PythonFactory) -> None:
        cut = await python()("list(range(5_000))")
        assert "998, 999, ...]" in cut.return_value
        assert cut.return_value.endswith(
            "print() it or write it to a file to get all of it]"
        )

        whole = await python()("'Loading...'")
        assert whole.return_value == "Result: 'Loading...'"

    async def test_blank_code_beside_a_script_runs_the_script(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        """An argument a model spelled empty is the one it did not use."""
        (tmp_path / "run.py").write_text("print('from the script')\n7")
        configured = python((SearchPath.of(path=tmp_path, target="~", implied=True),))

        result = await configured(code="", script_path="~/run.py")

        assert result.metadata.result == "7"
        assert result.metadata.stdout == "from the script"

    async def test_the_environment_mirrors_a_shell(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        configured = python(
            environ={"HOME": "/workspace/~", "USER": "u", "TMPDIR": "/tmp"}
        )

        result = await configured(
            "import os\n"
            "[os.getenv(k) for k in ('HOME', 'USER', 'PWD', 'TMPDIR', 'LANG')]"
            " + [os.getcwd()]"
        )

        assert result.metadata.result == (
            "['/workspace/~', 'u', '/workspace', '/tmp', 'C.UTF-8', '/workspace']"
        )

    async def test_an_empty_program_says_so(self, python: PythonFactory) -> None:
        with pytest.raises(Retry, match="program is empty"):
            await python()(code="   ")

    async def test_two_programs_are_refused(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        configured = python((SearchPath.of(path=tmp_path, target="~", implied=True),))
        # Named apart from the empty case, since a model told to "provide one
        # of" what it just provided both of looks for the fault elsewhere.
        with pytest.raises(Retry, match="both given"):
            await configured(code="1", script_path="~/run.py")

    async def test_statement_only_program_has_no_result(
        self, python: PythonFactory
    ) -> None:
        result = await python()("total = 1 + 1")
        assert result.metadata == PythonResult()
        assert "no value" in result.return_value

    async def test_failure_retries_with_traceback_and_prior_output(
        self, python: PythonFactory
    ) -> None:
        with pytest.raises(Retry, match="ZeroDivisionError") as exc_info:
            await python()("print('before')\n1 / 0")
        assert "before" in str(exc_info.value)

    async def test_time_limit_bounds_a_runaway_program(
        self, python: PythonFactory
    ) -> None:
        bounded = python(limits={"max_feed_duration_secs": 0.2})
        with pytest.raises(Retry, match="TimeoutError"):
            await bounded("while True:\n    pass")

    async def test_printed_output_is_kept_whole(self, python: PythonFactory) -> None:
        """The spill bounds the text, so the data a client renders keeps it all."""
        result = await python()("print('x' * 100_000)")

        assert result.metadata.stdout == "x" * 100_000

    async def test_oversized_document_is_refused_before_it_is_decoded(
        self, python: PythonFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The budget bounds host memory, so a file that cannot fit it must never
        # reach the decoder: a program with the workspace mounted can open every
        # document in it, one after the other.
        (tmp_path / "big.txt").write_text("x" * 1000)
        monkeypatch.setattr(
            workspace_os,
            "decode",
            lambda *args, **kwargs: pytest.fail("the document was decoded"),
        )
        bounded = python(
            (SearchPath.of(path=tmp_path, target="~", implied=True),),
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

        assert "~/big.txt" in str(result.metadata.result)

    async def test_stored_script_reads_the_mount_and_stages_its_writes(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "script.py").write_text(
            "from pathlib import Path\n"
            'text = Path("~/input.txt").read_text()\n'
            'Path("~/output.txt").write_text(text.upper())\n'
            'Path("~/output.txt").read_text()'
        )
        (tmp_path / "input.txt").write_text("hello")
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        result = await workspace_tool(script_path="~/script.py")

        assert [changes.operations for changes in staged] == [
            (Write("~/output.txt", "HELLO", "create"),)
        ]
        assert result.metadata.result == "'HELLO'"
        assert result.metadata.changeset == PendingChanges(
            "staged-id", ChangesetSummary(), 0
        )
        assert "changeset_id='staged-id'" in result.return_value
        assert not (tmp_path / "output.txt").exists()

    async def test_stored_script_is_reloaded_after_an_edit(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        script = tmp_path / "script.py"
        scoped = SearchPath.of(path=tmp_path, target="~", implied=True)
        workspace_tool = python((scoped,))

        script.write_text("1 + 1")
        first = await workspace_tool(script_path="~/script.py")
        script.write_text("1 + 2")
        second = await workspace_tool(script_path="~/script.py")

        assert first.metadata.result == "2"
        assert second.metadata.result == "3"

    async def test_program_discovers_a_document_it_was_never_told_about(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "one.md").write_text("alpha")
        (tmp_path / "notes" / "two.md").write_text("beta")
        workspace_tool = python(
            (SearchPath.of(path=tmp_path, target="~", implied=True),)
        )

        result = await workspace_tool(
            "from pathlib import Path\n"
            'sorted(p.read_text() for p in Path("~/notes").iterdir())'
        )

        assert result.metadata.result == "['alpha', 'beta']"

    async def test_renames_and_removals_stage_through_normal_path_calls(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "inbox").mkdir()

        for name in ("a", "b"):
            (tmp_path / "inbox" / f"{name}.md").write_text(name)

        (tmp_path / "stale.md").write_text("old")
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        result = await workspace_tool(
            "from pathlib import Path\n"
            'Path("~/archive").mkdir()\n'
            'for p in Path("~/inbox").iterdir():\n'
            '    p.rename(Path("~/archive") / p.name)\n'
            'Path("~/inbox").rmdir()\n'
            'Path("~/stale.md").unlink()\n'
            '[p.name for p in Path("~").iterdir()]'
        )

        assert result.metadata.result == "['archive']"
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
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        workspace_tool = python(
            (SearchPath.of(path=tmp_path, target="~", implied=True),)
        )

        with pytest.raises(Retry, match="cannot be changed"):
            await workspace_tool(
                'from pathlib import Path\nPath("~/state.json").write_text("{}")'
            )

        assert not (tmp_path / "state.json").exists()

    async def test_a_program_that_changes_nothing_stages_nothing(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "notes.md").write_text("same")
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        result = await workspace_tool(
            'text = open("~/notes.md").read()\nopen("~/notes.md", "w").write(text)'
        )

        assert staged == []
        assert result.metadata.changeset is None

    async def test_failed_program_stages_nothing(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        with pytest.raises(Retry, match="ZeroDivisionError"):
            await workspace_tool(
                'from pathlib import Path\nPath("~/output.txt").write_text("new")\n1 / 0'
            )

        assert staged == []
