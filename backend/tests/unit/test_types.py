"""Unit tests for the document scope: what it hides and what it only names."""

from pathlib import Path

import pytest
from exar.memory.mounts import Mount

from hivegent.agents.common import UserDeps
from hivegent.prompts import format_document_scope
from hivegent.server.common import parse_document_scope
from hivegent.store import (
    GroupScope,
    HostGrant,
    User,
    UserScope,
    WorkspaceScope,
)
from hivegent.types import DocumentFilter
from tests.helpers import doc, folder


class TestDocumentFilter:
    """Tests for DocumentFilter.__call__."""

    def test_empty_filter_includes_everything(self) -> None:
        f = DocumentFilter()
        assert f("any/file.md")

    def test_exclude_covers_logical_entry_siblings(self) -> None:
        f = DocumentFilter(excluded=frozenset({doc("report.md")}))
        assert not f("report.md")
        assert not f("report.pdf")
        assert not f("report.assets/img.png")
        assert f("reportfoo.md")

    def test_directory_exclude_covers_itself_and_subtree(self) -> None:
        f = DocumentFilter(excluded=frozenset({folder("drafts")}))
        assert f("report.md")
        assert not f("drafts")
        assert not f("drafts/wip.md")

    def test_root_entry_hides_the_whole_store(self) -> None:
        assert not DocumentFilter(excluded=frozenset({folder("")}))("any/file.md")


class TestDocumentFilterGlobs:
    """The `exclude` globs of a filter hide exactly what it hides, as the mount of a search path sees them."""

    PATHS = (
        "any/file.md",
        "docs/x.md",
        "docs/y.md",
        "docs/deep/x.md",
        "docs/other.md",
        "notes/a.md",
        "notes/a.pdf",
        "notes/a.v2.pdf",
        "notes/a.assets/fig.png",
        "notes/a.csv.cbrkit.json",
        "notes/b.md",
        "100%_done/a.md",
    )
    FILTERS = (
        DocumentFilter(),
        DocumentFilter(excluded=frozenset({folder("")})),
        DocumentFilter(excluded=frozenset({folder("docs"), folder("100%_done")})),
        DocumentFilter(excluded=frozenset({doc("notes/a.md")})),
        DocumentFilter(excluded=frozenset({doc("notes/a.v2.pdf")})),
    )

    @pytest.mark.parametrize("filter_", FILTERS)
    def test_globs_agree_with_the_predicate(
        self, filter_: DocumentFilter, tmp_path: Path
    ) -> None:
        for path in self.PATHS:
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).touch()

        mount = Mount(source=tmp_path, exclude=filter_.globs(tmp_path))

        assert {path for path in self.PATHS if mount.excludes(path)} == {
            path for path in self.PATHS if not filter_(path)
        }

    def test_globs_hide_the_parts_of_an_excluded_document_added_later(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "a.md").touch()
        (tmp_path / "notes" / "a.v2.md").touch()
        filter_ = DocumentFilter(excluded=frozenset({doc("notes/a.md")}))
        mount = Mount(source=tmp_path, exclude=filter_.globs(tmp_path))

        assert [
            mount.excludes(path)
            for path in ("notes/a.pdf", "notes/a.assets/fig.png", "notes/a.v2.md")
        ] == [True, True, False]


class TestParseDocumentScope:
    """Tests for parse_document_scope."""

    _user = User("u", read_groups=frozenset({"team"}))

    def test_no_entries_yields_no_selection(self) -> None:
        assert parse_document_scope([], [], self._user) == (frozenset(), {})

    def test_exclude_only_filters_the_store_it_names(self) -> None:
        _relevant, filters = parse_document_scope([], [folder("~/drafts")], self._user)
        user_filter = filters[UserScope("u")]
        assert not user_filter("drafts/wip.md")
        assert user_filter("report.md")
        assert list(filters) == [UserScope("u")]

    def test_include_restricts_nothing_and_comes_back_canonical(self) -> None:
        relevant, filters = parse_document_scope(
            [folder("~/docs"), doc("@team/notes.md"), doc("/srv/a.md")], [], self._user
        )
        assert relevant == frozenset({"~/docs/", "@team/notes.md", "/srv/a.md"})
        assert filters == {}

    def test_bare_scope_root_renders_as_the_workspace_root(self) -> None:
        relevant, _filters = parse_document_scope(
            [folder("~"), folder("/srv")], [], self._user
        )
        assert relevant == frozenset({"~/", "/srv/"})

    def test_unaddressable_entries_are_skipped(self) -> None:
        relevant, filters = parse_document_scope(
            [folder("@stranger/docs"), doc("no-prefix.md")],
            [doc("@stranger/secret.md")],
            self._user,
        )
        assert relevant == frozenset()
        assert filters == {}


class TestFormatDocumentScope:
    """Tests for the prompt-block renderer."""

    def test_nothing_selected_returns_empty(self) -> None:
        assert format_document_scope({}, frozenset(), "en") == ""

    def test_relevant_paths_are_named_as_a_hint(self) -> None:
        text = format_document_scope(
            {"~/a.md": "", "~/lab.md": "query_table runs SQL over it"},
            frozenset(),
            "en",
        )
        assert "Most relevant:" in text
        assert "- ~/a.md\n" in text
        assert "- ~/lab.md (query_table runs SQL over it)" in text
        assert "not a restriction" in text
        assert "Hidden from this conversation:" not in text

    def test_hidden_paths_are_named_as_unavailable(self) -> None:
        text = format_document_scope({}, frozenset({"~/secret.md"}), "en")
        assert "Hidden from this conversation:" in text
        assert "- ~/secret.md" in text
        assert "Most relevant:" not in text

    def test_both_halves_render_together(self) -> None:
        text = format_document_scope(
            {"~/docs/": ""}, frozenset({"~/docs/secret.md"}), "en"
        )
        assert "Most relevant:" in text
        assert "Hidden from this conversation:" in text


class TestDescribeDocumentScope:
    """Tests for UserDeps rendering its live selection to canonical paths."""

    @pytest.fixture(autouse=True)
    def _isolated(self, data_dir: Path) -> None:
        """Resolve the selection under a temporary workspace, never a real one."""
        _ = data_dir

    @staticmethod
    def _deps(
        relevant_documents: frozenset[str] = frozenset(),
        document_filters: dict[WorkspaceScope, DocumentFilter] | None = None,
    ) -> UserDeps:
        return UserDeps(
            user=User("u", write_groups=frozenset({"team"})),
            mode="interactive",
            document_filters=document_filters or {},
            relevant_documents=relevant_documents,
        )

    def test_no_selection_is_empty(self) -> None:
        assert self._deps().describe_document_scope() == ""

    def test_relevant_documents_pass_through(self) -> None:
        text = self._deps(
            relevant_documents=frozenset({"~/a/r.md", "@team/docs/"})
        ).describe_document_scope()
        assert "- ~/a/r.md" in text
        assert "- @team/docs/" in text

    def test_selected_projection_names_the_original_it_came_from(
        self, data_dir: Path
    ) -> None:
        # A spreadsheet is only ever selected under the markdown it was
        # projected to, so the block is where the run learns query_table has
        # the original to work from.
        workspace = HostGrant().workspace(UserScope("u")).workspace_dir()
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "lab.md").write_text("| a |\n|---|\n")
        (workspace / "lab.xlsx").write_bytes(b"PK\x03\x04")
        (workspace / "notes.md").write_text("prose")

        text = self._deps(
            relevant_documents=frozenset({"~/lab.md", "~/notes.md"})
        ).describe_document_scope()

        assert "- ~/lab.md (query_table runs SQL over it" in text
        assert "'~/lab.xlsx'" in text
        assert "- ~/notes.md\n" in text

    def test_group_excludes_render_with_group_prefix(self) -> None:
        text = self._deps(
            document_filters={
                GroupScope("team"): DocumentFilter(excluded=frozenset({folder("old")}))
            }
        ).describe_document_scope()
        assert "- @team/old/" in text

    def test_root_exclude_renders_as_workspace_root(self) -> None:
        text = self._deps(
            document_filters={
                UserScope("u"): DocumentFilter(excluded=frozenset({folder("")}))
            }
        ).describe_document_scope()
        assert "- ~/" in text

    def test_rendering_is_deterministic_across_calls(self) -> None:
        # Sorted output keeps the cached prompt prefix byte-identical between
        # turns when the selection is unchanged.
        deps = self._deps(relevant_documents=frozenset({"~/b.md", "~/a.md", "~/c.md"}))
        assert deps.describe_document_scope() == deps.describe_document_scope()
