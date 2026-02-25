"""Unit tests for DocumentFilter."""

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

    def test_directory_prefix_include(self) -> None:
        f = DocumentFilter(included=frozenset({"projects/"}))
        assert f("projects/report.md")
        assert f("projects/sub/file.md")
        assert not f("other/file.md")

    def test_exclude_overrides_include(self) -> None:
        f = DocumentFilter(
            included=frozenset({"projects/"}),
            excluded=frozenset({"projects/secret.md"}),
        )
        assert f("projects/report.md")
        assert not f("projects/secret.md")

    def test_exclude_only(self) -> None:
        f = DocumentFilter(excluded=frozenset({"private.md"}))
        assert f("report.md")
        assert not f("private.md")

    def test_exclude_directory_prefix(self) -> None:
        f = DocumentFilter(excluded=frozenset({"drafts/"}))
        assert f("report.md")
        assert not f("drafts/wip.md")
