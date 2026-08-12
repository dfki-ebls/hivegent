"""Document chunking and persistence coordinator.

The high-level pipeline lives here: chunker → SQL upsert of the
``Document`` row (``content_digest`` cleared) → cbrkit ``replace_where`` for
chunks (embed, then DELETE + INSERT in one cbrkit transaction) → stamp
``content_digest`` last.  These are separate transactions, so stamping the
digest last makes a null digest the uniform "not indexed" marker for anything
the sequence never finished.  Deletes flow through
:mod:`hivegent.db.documents` and cascade via FK to chunks.
"""

import logging
from collections.abc import Sequence

import logfire

from .chunkers import ChunkingPipeline, ChunkingSpec, get_chunker
from .chunkers.base import (
    ChunkData,
    ChunkSummary,
    DocumentMetadata,
    EntryMetadata,
)
from .config import content_digest, settings
from .db import documents as db_documents
from .entries import (
    ContentStat,
    resolve_entry_paths,
    stem_path_from_reference,
)
from .retrieval import index_document
from .store import Casebase

__all__ = [
    "ChunkData",
    "ChunkSummary",
    "DocumentMetadata",
    "chunk_and_index_document",
    "delete_document",
    "delete_documents",
]

logger = logging.getLogger(__name__)


# ─── Writer / coordinator ─────────────────────────────────────────────


def _default_entry_metadata(
    filename: str,
    resolved_original_path: str | None,
    resolved_assets_dir: str | None,
) -> EntryMetadata:
    """Build the default logical-entry metadata for a markdown file."""
    import mimetypes

    files = [filename]
    if resolved_original_path is not None:
        files.append(resolved_original_path)
    return EntryMetadata(
        entry_kind="user_markdown",
        stem_path=stem_path_from_reference(filename),
        description_path=filename,
        original_path=resolved_original_path,
        assets_dir=resolved_assets_dir,
        mime=mimetypes.guess_type(resolved_original_path or filename)[0],
        origin="upload",
        generated_by="user",
        files=files,
    )


async def chunk_and_index_document(
    store: Casebase,
    filename: str,
    content: str,
    chunking: ChunkingSpec | None = None,
    *,
    stat: ContentStat | None,
    entry_metadata: EntryMetadata | None = None,
) -> DocumentMetadata:
    """Chunk a document, embed it, and persist the row, its chunks, and digest.

    Not one transaction but an ordered sequence of them: the ``Document`` row is
    upserted with its ``content_digest`` cleared, the chunk rows are written
    (embedded first, then DELETE + INSERT in cbrkit's own transaction), and the
    digest is stamped last by
    :func:`hivegent.db.documents.set_content_state`.  Stamping it last
    makes a null ``content_digest`` the uniform "not indexed" marker: if the
    sequence is torn by a crash, the surviving row is re-indexed on the next
    startup reconcile.

    *stat* is the on-disk ``(mtime, size)`` fingerprint of *content*, captured
    by the caller when it read or wrote the file so the stamped stat matches the
    indexed bytes without this coordinator reaching back to disk for it.
    """
    spec = chunking or ChunkingSpec()

    if entry_metadata is None:
        entry_metadata = await db_documents.get_entry_metadata(store, filename)
        if entry_metadata is None:
            workspace_dir = store.workspace_dir(settings.data_dir)
            resolved = resolve_entry_paths(workspace_dir, filename)
            entry_metadata = _default_entry_metadata(
                resolved.description_path,
                resolved.original_path,
                resolved.assets_dir,
            )

    if entry_metadata.generated_by in ("vision", "stub"):
        spec = ChunkingSpec(pipeline=ChunkingPipeline.NONE)

    with logfire.span(
        "chunk_and_index_document",
        store_key=store.store_key,
        filename=filename,
        content_length=len(content),
        pipeline=spec.pipeline.value,
        entry_kind=entry_metadata.entry_kind,
    ) as span:
        chunker = get_chunker(spec.pipeline, config=spec.config)
        raw_chunks = await chunker(content, mime=entry_metadata.mime)
        span.set_attribute("chunker", chunker.name)
        span.set_attribute("chunk_count", len(raw_chunks))

        digest = content_digest(content)
        doc = await db_documents.upsert_document(
            store,
            entry_metadata,
            pipeline=chunker.name,
            line_count=len(content.splitlines()),
        )
        await index_document(doc.id, raw_chunks)
        await db_documents.set_content_state(doc.id, digest, stat)
        return doc.model_copy(
            update={"chunks": list(raw_chunks), "content_digest": digest}
        )


async def delete_document(store: Casebase, filepath: str) -> bool:
    """Remove a document and its chunks (vectors cascade via FK)."""
    return await db_documents.delete_document(store, filepath)


async def delete_documents(store: Casebase, filepaths: Sequence[str]) -> int:
    """Remove many documents and their chunks in one statement."""
    return await db_documents.delete_documents(store, filepaths)
