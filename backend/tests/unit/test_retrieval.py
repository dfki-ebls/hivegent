"""Unit tests for retrieval helpers."""

import pytest
from inline_snapshot import snapshot

from hivegent.retrieval import _build_where_clause, parse_chunk_key
from hivegent.types import DocumentFilter


class TestParseChunkKey:
    """Tests for parse_chunk_key."""

    def test_valid_simple(self) -> None:
        filename, index = parse_chunk_key("report.md::3")
        assert filename == "report.md"
        assert index == 3

    def test_valid_nested_path(self) -> None:
        filename, index = parse_chunk_key("projects/sub/file.md::0")
        assert filename == "projects/sub/file.md"
        assert index == 0

    def test_invalid_no_separator(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk key"):
            parse_chunk_key("report.md")

    def test_invalid_non_integer_index(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk key"):
            parse_chunk_key("report.md::abc")


class TestBuildWhereClause:
    """Tests for _build_where_clause."""

    def test_no_filter(self) -> None:
        assert _build_where_clause(None) is None

    def test_empty_filter(self) -> None:
        assert _build_where_clause(DocumentFilter()) is None

    def test_include_exact(self) -> None:
        f = DocumentFilter(included=frozenset({"report.md"}))
        assert _build_where_clause(f) == snapshot(
            "(filename = 'report.md')"
        )

    def test_include_directory(self) -> None:
        f = DocumentFilter(included=frozenset({"projects/"}))
        assert _build_where_clause(f) == snapshot(
            "(filename LIKE 'projects/%')"
        )

    def test_exclude_exact(self) -> None:
        f = DocumentFilter(excluded=frozenset({"secret.md"}))
        assert _build_where_clause(f) == snapshot(
            "filename != 'secret.md'"
        )

    def test_exclude_directory(self) -> None:
        f = DocumentFilter(excluded=frozenset({"private/"}))
        assert _build_where_clause(f) == snapshot(
            "filename NOT LIKE 'private/%'"
        )

    def test_include_and_exclude(self) -> None:
        f = DocumentFilter(
            included=frozenset({"docs/"}),
            excluded=frozenset({"docs/secret.md"}),
        )
        result = _build_where_clause(f)
        assert result is not None
        assert "LIKE 'docs/%'" in result
        assert "!= 'docs/secret.md'" in result

    def test_sql_escaping(self) -> None:
        f = DocumentFilter(included=frozenset({"it's.md"}))
        result = _build_where_clause(f)
        assert result is not None
        assert "it''s.md" in result
