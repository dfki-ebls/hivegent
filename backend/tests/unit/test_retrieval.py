"""Unit tests for retrieval helpers."""

import pytest
from inline_snapshot import snapshot

import hivegent.retrieval as retrieval
import hivegent.workspace as workspace
from hivegent.chunkers.base import RetrievedChunk
from hivegent.retrieval import (
    _ChunkEntry,
    _parse_chunk_key,
    _to_retrieved_chunk,
    sync_index,
)
from hivegent.store import Casebase
from hivegent.tools.retrieval import SearchResult
from hivegent.types import PipelineSpec


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
        token_count: int = 2,
        start_line: int = 1,
        end_line: int = 1,
        start_index: int = 0,
        end_index: int = 11,
    ) -> _ChunkEntry:
        return _ChunkEntry(
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
        chunk = _to_retrieved_chunk(result, self._meta(token_count=3))
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


async def test_move_document_removes_old_index_key(
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
) -> None:
    """Moving a document removes the old description-path row from LanceDB."""
    _ = fake_embeddings
    await workspace.upload(
        user_store,
        "old.md",
        b"legacy content",
        spec=single_chunk_pipeline,
    )

    await workspace.move_document(user_store, "old.md", "new.md")

    storage = retrieval._state.get_storage(user_store)
    assert sorted(storage.index) == ["new.md::0"]


async def test_delete_directory_with_wildcard_keeps_sibling_index(
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
) -> None:
    """Deleting a directory with SQL wildcard characters only removes its subtree."""
    _ = fake_embeddings
    await workspace.upload(
        user_store, "a_/inside.md", b"inside", spec=single_chunk_pipeline
    )
    await workspace.upload(
        user_store, "ab/outside.md", b"outside", spec=single_chunk_pipeline
    )

    await workspace.delete_directory(user_store, "a_")

    storage = retrieval._state.get_storage(user_store)
    assert sorted(storage.index) == ["ab/outside.md::0"]


async def test_delete_document_with_quote_in_path_cleans_index(
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
) -> None:
    """Deleting paths that need SQL escaping still removes their index rows."""
    _ = fake_embeddings
    await workspace.upload(
        user_store, "quote's.md", b"quoted", spec=single_chunk_pipeline
    )

    await workspace.delete_document(user_store, "quote's.md")

    storage = retrieval._state.get_storage(user_store)
    assert storage.index == {}


def test_sync_index_empty_store_does_not_create_empty_table(
    user_store: Casebase,
    fake_embeddings: None,
) -> None:
    """Syncing an empty store is a no-op instead of creating an invalid table."""
    _ = fake_embeddings
    sync_index(user_store)

    storage = retrieval._state.get_storage(user_store)
    assert not storage.has_index()
