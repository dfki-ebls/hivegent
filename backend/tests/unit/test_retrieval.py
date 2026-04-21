"""Unit tests for retrieval helpers."""

import pytest
from inline_snapshot import snapshot

from hivegent.chunkers.base import RetrievedChunk
from hivegent.retrieval import _ChunkEntry, _parse_chunk_key, _to_retrieved_chunk
from hivegent.tools.retrieval import SearchResult


class TestParseChunkKey:
    """Tests for _parse_chunk_key."""

    def test_valid_simple(self) -> None:
        filename, index = _parse_chunk_key("report.md::3")
        assert filename == "report.md"
        assert index == 3

    def test_valid_nested_path(self) -> None:
        filename, index = _parse_chunk_key("projects/sub/file.md::0")
        assert filename == "projects/sub/file.md"
        assert index == 0

    def test_invalid_no_separator(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk key"):
            _parse_chunk_key("report.md")

    def test_invalid_non_integer_index(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk key"):
            _parse_chunk_key("report.md::abc")


class TestToRetrievedChunk:
    """Tests for the _to_retrieved_chunk result mapper."""

    def _meta(
        self,
        *,
        text: str = "hello world",
        token_count: int = 2,
        start_line: int = 1,
        end_line: int = 1,
        start_index: int = 0,
        end_index: int = 11,
    ) -> _ChunkEntry:
        return _ChunkEntry(
            text=text,
            token_count=token_count,
            start_line=start_line,
            end_line=end_line,
            start_index=start_index,
            end_index=end_index,
        )

    def test_transforms_result(self) -> None:
        result = SearchResult(key="report.md::0", text="hello world", score=0.95)
        chunk = _to_retrieved_chunk(result, self._meta())
        assert chunk == snapshot(
            RetrievedChunk(
                filename="report.md",
                chunk_index=0,
                text="hello world",
                token_count=2,
                score=0.95,
                start_line=1,
                end_line=1,
                start_index=0,
                end_index=11,
            )
        )

    def test_nested_path(self) -> None:
        result = SearchResult(key="docs/notes.md::2", text="foo bar baz", score=0.80)
        chunk = _to_retrieved_chunk(
            result, self._meta(text="foo bar baz", token_count=3)
        )
        assert chunk == snapshot(
            RetrievedChunk(
                filename="docs/notes.md",
                chunk_index=2,
                text="foo bar baz",
                token_count=3,
                score=0.8,
                start_line=1,
                end_line=1,
                start_index=0,
                end_index=11,
            )
        )

    def test_carries_metadata(self) -> None:
        result = SearchResult(key="report.md::0", text="hello world", score=0.95)
        chunk = _to_retrieved_chunk(
            result,
            _ChunkEntry(
                text="hello world",
                token_count=42,
                start_line=5,
                end_line=10,
                start_index=100,
                end_index=200,
            ),
        )
        assert chunk == snapshot(
            RetrievedChunk(
                filename="report.md",
                chunk_index=0,
                text="hello world",
                token_count=42,
                score=0.95,
                start_line=5,
                end_line=10,
                start_index=100,
                end_index=200,
            )
        )

    def test_rounds_score(self) -> None:
        result = SearchResult(key="a.md::1", text="x", score=0.123456789)
        assert _to_retrieved_chunk(result, self._meta()).score == snapshot(0.1235)
