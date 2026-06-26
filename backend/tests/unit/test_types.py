"""Unit tests for DocumentFilter and its request-level parsing."""

from hivegent.agents.common import UserDeps
from hivegent.prompts import format_document_scope
from hivegent.server.common import parse_document_filters
from hivegent.store import Casebase
from hivegent.types import DocumentFilter


class TestDocumentFilter:
    """Tests for DocumentFilter.__call__."""

    def test_empty_filter_includes_everything(self) -> None:
        f = DocumentFilter()
        assert f("any/file.md")

    def test_exact_include(self) -> None:
        f = DocumentFilter(included=frozenset({"report.md"}))
        assert f("report.md")
        assert not f("other.md")

    def test_include_covers_logical_entry_siblings(self) -> None:
        f = DocumentFilter(included=frozenset({"docs/report.md"}))
        assert f("docs/report.md")
        assert f("docs/report.pdf")
        assert f("docs/report.assets")
        assert f("docs/report.assets/img/fig1.png")
        assert not f("docs/other.md")

    def test_include_keeps_ancestor_directories_visible(self) -> None:
        f = DocumentFilter(included=frozenset({"a/b/report.md"}))
        assert f("a")
        assert f("a/b")
        assert not f("a/c")

    def test_directory_include_covers_itself_and_subtree(self) -> None:
        f = DocumentFilter(included=frozenset({"projects/"}))
        assert f("projects")
        assert f("projects/report.md")
        assert f("projects/sub/file.md")
        assert not f("other/file.md")
        assert not f("projectsfoo/file.md")

    def test_specific_exclude_overrides_directory_include(self) -> None:
        f = DocumentFilter(
            included=frozenset({"projects/"}),
            excluded=frozenset({"projects/secret.md"}),
        )
        assert f("projects/report.md")
        assert not f("projects/secret.md")

    def test_specific_include_overrides_directory_exclude(self) -> None:
        f = DocumentFilter(
            included=frozenset({"projects/keep.md"}),
            excluded=frozenset({"projects/"}),
        )
        assert f("projects/keep.md")
        # The excluded directory stays traversable to reach the include.
        assert f("projects")
        assert not f("projects/other.md")

    def test_nested_include_exclude_alternation(self) -> None:
        f = DocumentFilter(
            included=frozenset({"a/", "a/b/c/"}),
            excluded=frozenset({"a/b/"}),
        )
        assert f("a/file.md")
        assert not f("a/b/file.md")
        assert f("a/b/c/file.md")

    def test_exclude_only(self) -> None:
        f = DocumentFilter(excluded=frozenset({"private.md"}))
        assert f("report.md")
        assert not f("private.md")

    def test_exclude_covers_logical_entry_siblings(self) -> None:
        f = DocumentFilter(excluded=frozenset({"report.md"}))
        assert not f("report.pdf")
        assert not f("report.assets/img.png")
        assert f("reportfoo.md")

    def test_directory_exclude_covers_itself_and_subtree(self) -> None:
        f = DocumentFilter(excluded=frozenset({"drafts/"}))
        assert f("report.md")
        assert not f("drafts")
        assert not f("drafts/wip.md")

    def test_root_entry_selects_whole_store(self) -> None:
        assert DocumentFilter(included=frozenset({"/"}))("any/file.md")
        assert not DocumentFilter(excluded=frozenset({"/"}))("any/file.md")

    def test_empty_include_set_hides_everything(self) -> None:
        f = DocumentFilter(included=frozenset())
        assert not f("report.md")


class TestParseDocumentFilters:
    """Tests for parse_document_filters."""

    def test_no_entries_yields_no_filters(self) -> None:
        user_filter, group_filters = parse_document_filters([], [], frozenset())
        assert user_filter is None
        assert group_filters == {}

    def test_exclude_only_leaves_other_stores_unrestricted(self) -> None:
        user_filter, group_filters = parse_document_filters(
            [], ["~/drafts/"], frozenset({"team"})
        )
        assert user_filter is not None
        assert not user_filter("drafts/wip.md")
        assert user_filter("report.md")
        assert group_filters == {}

    def test_include_whitelists_across_all_stores(self) -> None:
        user_filter, group_filters = parse_document_filters(
            ["~/docs/"], [], frozenset({"team"})
        )
        assert user_filter is not None
        assert user_filter("docs/report.md")
        assert not user_filter("other.md")
        # The group store was not whitelisted, so it is hidden entirely.
        assert not group_filters["team"]("anything.md")

    def test_bare_scope_root_selects_whole_store(self) -> None:
        user_filter, group_filters = parse_document_filters(
            ["~"], [], frozenset({"team"})
        )
        assert user_filter is not None
        assert user_filter("any/file.md")
        assert not group_filters["team"]("any/file.md")

    def test_skipped_include_entries_fail_closed(self) -> None:
        user_filter, group_filters = parse_document_filters(
            ["@stranger/docs/", "no-prefix.md"], [], frozenset({"team"})
        )
        # Whitelist intent was expressed but no entry survived, so every
        # store is hidden rather than left unrestricted.
        assert user_filter is not None
        assert not user_filter("docs/report.md")
        assert not group_filters["team"]("docs/report.md")


class TestFormatDocumentScope:
    """Tests for the prompt-block renderer."""

    def test_unrestricted_returns_empty(self) -> None:
        assert format_document_scope(None, frozenset()) == ""

    def test_whitelist_lists_included_paths(self) -> None:
        text = format_document_scope(frozenset({"~/a.md", "~/b.md"}), frozenset())
        assert "In scope:" in text
        assert "- ~/a.md" in text
        assert "- ~/b.md" in text
        assert "Hidden from this conversation:" not in text

    def test_whitelist_with_carveout_lists_both(self) -> None:
        text = format_document_scope(
            frozenset({"~/docs/"}), frozenset({"~/docs/secret.md"})
        )
        assert "In scope:" in text
        assert "Carved out of the documents above:" in text
        assert "- ~/docs/secret.md" in text

    def test_exclude_only_lists_hidden_paths(self) -> None:
        text = format_document_scope(None, frozenset({"~/secret.md"}))
        assert "Hidden from this conversation:" in text
        assert "- ~/secret.md" in text
        assert "In scope:" not in text

    def test_empty_whitelist_states_nothing_in_scope(self) -> None:
        text = format_document_scope(frozenset(), frozenset())
        assert "No documents are currently in scope." in text


class TestDescribeDocumentScope:
    """Tests for UserDeps rendering its live filters to canonical paths."""

    @staticmethod
    def _deps(
        document_filter: DocumentFilter | None = None,
        group_filters: dict[str, DocumentFilter] | None = None,
    ) -> UserDeps:
        return UserDeps(
            user_id="u",
            store=Casebase.for_user("u"),
            group_stores=(Casebase.for_group("team"),),
            document_filter=document_filter,
            group_filters=group_filters or {},
        )

    def test_no_filter_is_empty(self) -> None:
        assert self._deps().describe_document_scope() == ""

    def test_user_includes_render_as_canonical_paths(self) -> None:
        text = self._deps(
            document_filter=DocumentFilter(included=frozenset({"docs/", "a/r.md"}))
        ).describe_document_scope()
        assert "- ~/a/r.md" in text
        assert "- ~/docs/" in text

    def test_group_excludes_render_with_group_prefix(self) -> None:
        text = self._deps(
            group_filters={"team": DocumentFilter(excluded=frozenset({"old/"}))}
        ).describe_document_scope()
        assert "- @team/old/" in text

    def test_root_entry_renders_as_workspace_root(self) -> None:
        text = self._deps(
            document_filter=DocumentFilter(included=frozenset({"/"}))
        ).describe_document_scope()
        assert "- ~/" in text

    def test_rendering_is_deterministic_across_calls(self) -> None:
        # Sorted output keeps the cached prompt prefix byte-identical between
        # turns when the selection is unchanged.
        deps = self._deps(
            document_filter=DocumentFilter(included=frozenset({"b.md", "a.md", "c.md"}))
        )
        assert deps.describe_document_scope() == deps.describe_document_scope()
