"""Unit tests for document chunking coordination."""

import pytest

from hivegent import chunks
from hivegent.metadata import EntryMetadata
from hivegent.store import Workspace
from tests.helpers import FakeIndex, entry

ENTRY = entry("doc")


async def _chunk_and_index(store: Workspace, metadata: EntryMetadata = ENTRY) -> None:
    await chunks.chunk_and_index_document(
        store, "doc.md", "body", stat=None, entry_metadata=metadata
    )


async def test_chunk_and_index_stamps_the_digest_last(
    user_store: Workspace, index: FakeIndex
) -> None:
    await _chunk_and_index(user_store)

    state = index.rows[user_store.store_key, "doc"].state
    assert state.content_digest is not None
    assert [e.stem_path for e in index.indexed] == ["doc"]


async def test_chunk_and_index_does_not_stamp_digest_after_index_failure(
    user_store: Workspace, index: FakeIndex
) -> None:
    index.failing = {"doc"}

    with pytest.raises(RuntimeError, match="chunks failed to write"):
        await _chunk_and_index(user_store)

    assert index.rows[user_store.store_key, "doc"].state.content_digest is None


async def test_a_failing_chunker_leaves_the_document_row_untouched(
    user_store: Workspace, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def chunk(text: str) -> list[object]:
        raise RuntimeError(f"cannot chunk {text}")

    monkeypatch.setattr(chunks, "get_chunker", lambda _pipeline: chunk)

    with pytest.raises(RuntimeError, match="cannot chunk"):
        await _chunk_and_index(user_store)

    assert index.rows == {}
