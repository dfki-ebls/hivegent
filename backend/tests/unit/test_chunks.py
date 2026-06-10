"""Unit tests for document chunking coordination."""

from datetime import UTC, datetime

import pytest

from hivegent import chunks
from hivegent.chunkers import ChunkingPipeline, ChunkingSpec
from hivegent.chunkers.base import DocumentMetadata, EntryMetadata
from hivegent.store import Casebase


def _entry_metadata() -> EntryMetadata:
    return EntryMetadata(
        entry_kind="user_markdown",
        stem_path="doc",
        description_path="doc.md",
        original_path=None,
        assets_dir=None,
        mime="text/markdown",
        origin="upload",
        generated_by="user",
        files=["doc.md"],
    )


def _document_metadata(pipeline: str) -> DocumentMetadata:
    return DocumentMetadata(
        **_entry_metadata().model_dump(),
        id="doc-id",
        pipeline=pipeline,
        created_at=datetime.now(UTC),
        chunks=[],
        content_digest=None,
    )


async def test_chunk_and_index_does_not_stamp_digest_after_index_failure(
    user_store: Casebase, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamped = False

    async def upsert_document(
        store: Casebase, entry: EntryMetadata, pipeline: str
    ) -> DocumentMetadata:
        _ = store, entry
        return _document_metadata(pipeline)

    async def index_document(document_id: str, raw_chunks: object) -> None:
        _ = document_id, raw_chunks
        raise RuntimeError("index failed")

    async def set_content_state(document_id: str, digest: str, stat: object) -> None:
        nonlocal stamped
        _ = document_id, digest, stat
        stamped = True

    monkeypatch.setattr(chunks.db_documents, "upsert_document", upsert_document)
    monkeypatch.setattr(chunks, "index_document", index_document)
    monkeypatch.setattr(chunks.db_documents, "set_content_state", set_content_state)

    with pytest.raises(RuntimeError, match="index failed"):
        await chunks.chunk_and_index_document(
            user_store,
            "doc.md",
            "body",
            ChunkingSpec(pipeline=ChunkingPipeline.NONE),
            stat=None,
            entry_metadata=_entry_metadata(),
        )

    assert stamped is False
