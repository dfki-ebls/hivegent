"""Document chunking and persistence coordinator, by the stamp-last protocol of `exar.ingest.index_document`.

exar chunks the text first, so a chunker that fails leaves the index as it
was.  Then the SQL upsert of the `Document` row (`content_digest` and
`chunker` cleared) comes, the chunks are written through cbrkit (embedded,
then DELETE and INSERT in one cbrkit transaction), and the digest of the text
and the fingerprint of the chunker are stamped last.  These are separate
transactions, so stamping last makes a null digest the uniform "not indexed"
marker for anything the sequence never finished, and a chunker fingerprint
other than `chunking`'s marks chunks made with other options.  Deletes flow
through `hivegent.db.documents` and cascade via FK to chunks.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

import logfire
from exar.ingest import Chunk, index_document

from . import retrieval
from .db import documents as db_documents
from .metadata import ChunkData, DocumentMetadata, EntryMetadata
from .pipelines import (
    AUTO,
    DEFAULT_CHUNKING,
    NO_CHUNKING,
    chunker_fingerprint,
    get_chunker,
)
from .sources.catalog import chunking_of
from .store import Workspace
from .tools.entries import ContentStat

__all__ = [
    "ChunkData",
    "DocumentMetadata",
    "chunk_and_index_document",
    "chunking",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class _DocumentTable:
    """The document and chunk tables as an `exar.ingest.ChunkStore` of one *entry*, keyed by its workspace.

    The row is upserted with its digest cleared only once exar has made the
    chunks, and the digest is stamped with the on-disk *stat*.
    """

    entry: EntryMetadata
    pipeline: str
    line_count: int
    stat: ContentStat | None
    _ids: dict[str, UUID] = field(default_factory=dict)

    async def replace(self, key: Workspace, chunks: Sequence[Chunk], /) -> None:
        doc = await db_documents.upsert_document(
            key, self.entry, pipeline=self.pipeline, line_count=self.line_count
        )
        self._ids[key.store_key] = doc.id
        await retrieval.index_document(doc.id, chunks)

    async def stamp(self, key: Workspace, digest: str, chunker: str, /) -> None:
        await db_documents.set_content_state(
            self._ids[key.store_key], digest, chunker, self.stat
        )


def chunking(store: Workspace, entry: EntryMetadata) -> str:
    """The chunking pipeline of an entry of *store*, whole for a generated description."""
    if entry.generated_by in ("vision", "stub"):
        return NO_CHUNKING

    pipeline = chunking_of(store)

    return DEFAULT_CHUNKING if pipeline == AUTO else pipeline


async def chunk_and_index_document(
    store: Workspace,
    filename: str,
    content: str,
    *,
    stat: ContentStat | None,
    entry_metadata: EntryMetadata,
) -> None:
    """Chunk a document, embed it, and persist the row, its chunks, and digest.

    Not one transaction but an ordered sequence of them: `exar.ingest.index_document`
    chunks the text, which a failing chunker ends with the index untouched,
    then the ``Document`` row is upserted with its ``content_digest`` cleared,
    the chunk rows are written (embedded first, then DELETE + INSERT in
    cbrkit's own transaction), and the digest is stamped last by
    :func:`hivegent.db.documents.set_content_state`.  Stamping it last
    makes a null ``content_digest`` the uniform "not indexed" marker: if the
    sequence is torn by a crash, the surviving row is re-indexed on the next
    startup reconcile.

    *stat* is the on-disk ``(mtime, size)`` fingerprint of *content*, captured
    by the caller when it read or wrote the file so the stamped stat matches the
    indexed bytes without this coordinator reaching back to disk for it, and
    *entry_metadata* is the row it upserts.
    """
    pipeline = chunking(store, entry_metadata)

    with logfire.span(
        "chunk_and_index_document",
        store_key=store.store_key,
        filename=filename,
        content_length=len(content),
        pipeline=pipeline,
        entry_kind=entry_metadata.entry_kind,
    ) as span:
        table = _DocumentTable(
            entry=entry_metadata,
            pipeline=pipeline,
            line_count=len(content.splitlines()),
            stat=stat,
        )
        ingested = await index_document(
            store,
            content,
            get_chunker(pipeline),
            table,
            fingerprint=chunker_fingerprint(pipeline),
        )
        span.set_attribute("chunk_count", len(ingested.chunks))

    logger.info(
        "Indexed %s/%s into %d chunks",
        store.store_key,
        filename,
        len(ingested.chunks),
    )
