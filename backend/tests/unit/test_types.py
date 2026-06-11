"""Unit tests for DocumentFilter and its request-level parsing."""

from hivegent.server.common import parse_document_filters
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
