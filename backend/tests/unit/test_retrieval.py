"""Unit tests for retrieval helpers."""

import pytest

import hivegent.retrieval as retrieval
import hivegent.workspace as workspace
from hivegent.retrieval import _build_key, _parse_key
from hivegent.store import Casebase
from hivegent.types import PipelineSpec


class TestParseKey:
    """Tests for ``_parse_key`` / ``_build_key`` round-tripping."""

    def test_user_key_round_trip(self) -> None:
        store = Casebase(kind="user", id="alice")
        key = _build_key(store, "report.md", 3)
        parsed_store, filename, index = _parse_key(key)
        assert parsed_store == store
        assert filename == "report.md"
        assert index == 3

    def test_group_key_with_nested_path(self) -> None:
        store = Casebase(kind="group", id="team-1")
        key = _build_key(store, "projects/sub/file.md", 0)
        parsed_store, filename, index = _parse_key(key)
        assert parsed_store == store
        assert filename == "projects/sub/file.md"
        assert index == 0

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk key"):
            _parse_key("garbage")

    def test_invalid_kind(self) -> None:
        with pytest.raises(ValueError, match="Invalid chunk key"):
            _parse_key("admin:1::file.md::0")


async def test_move_document_removes_old_index_key(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
) -> None:
    """Moving a document removes the old description-path row from LanceDB."""
    _ = (db_initialized, fake_embeddings)
    await workspace.upload(
        user_store,
        "old.md",
        b"legacy content",
        spec=single_chunk_pipeline,
    )

    await workspace.move_document(user_store, "old.md", "new.md")

    storage = retrieval._state.get_storage()
    keys = sorted(storage.index)
    assert keys == [_build_key(user_store, "new.md", 0)]


async def test_delete_directory_with_wildcard_keeps_sibling_index(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
) -> None:
    """Deleting a directory with SQL wildcard characters only removes its subtree."""
    _ = (db_initialized, fake_embeddings)
    await workspace.upload(
        user_store, "a_/inside.md", b"inside", spec=single_chunk_pipeline
    )
    await workspace.upload(
        user_store, "ab/outside.md", b"outside", spec=single_chunk_pipeline
    )

    await workspace.delete_directory(user_store, "a_")

    storage = retrieval._state.get_storage()
    assert sorted(storage.index) == [_build_key(user_store, "ab/outside.md", 0)]


async def test_delete_document_with_quote_in_path_cleans_index(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
) -> None:
    """Deleting paths that need SQL escaping still removes their index rows."""
    _ = (db_initialized, fake_embeddings)
    await workspace.upload(
        user_store, "quote's.md", b"quoted", spec=single_chunk_pipeline
    )

    await workspace.delete_document(user_store, "quote's.md")

    storage = retrieval._state.get_storage()
    assert storage.index == {}
