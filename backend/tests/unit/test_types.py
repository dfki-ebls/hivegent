"""Unit tests for DocumentFilter.is_included."""

from hivegent.types import DocumentFilter


class TestDocumentFilterIsIncluded:
    """Tests for DocumentFilter.is_included."""

    def test_empty_filter_includes_everything(self) -> None:
        f = DocumentFilter()
        assert f.is_included("any/file.md")

    def test_exact_include(self) -> None:
        f = DocumentFilter(included=frozenset({"report.md"}))
        assert f.is_included("report.md")
        assert not f.is_included("other.md")

    def test_directory_prefix_include(self) -> None:
        f = DocumentFilter(included=frozenset({"projects/"}))
        assert f.is_included("projects/report.md")
        assert f.is_included("projects/sub/file.md")
        assert not f.is_included("other/file.md")

    def test_exclude_overrides_include(self) -> None:
        f = DocumentFilter(
            included=frozenset({"projects/"}),
            excluded=frozenset({"projects/secret.md"}),
        )
        assert f.is_included("projects/report.md")
        assert not f.is_included("projects/secret.md")

    def test_exclude_only(self) -> None:
        f = DocumentFilter(excluded=frozenset({"private.md"}))
        assert f.is_included("report.md")
        assert not f.is_included("private.md")

    def test_exclude_directory_prefix(self) -> None:
        f = DocumentFilter(excluded=frozenset({"drafts/"}))
        assert f.is_included("report.md")
        assert not f.is_included("drafts/wip.md")
