"""The metadata of indexed entries and their chunks, as the API and the database see them."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from exar.ingest import Chunk
from pydantic import BaseModel, Field

__all__ = [
    "ChunkData",
    "DocumentMetadata",
    "EntryGeneratedBy",
    "EntryKind",
    "EntryMetadata",
    "EntryOrigin",
]


class ChunkData(BaseModel):
    """A single chunk used by chunkers and persisted chunk metadata."""

    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    start_index: int = Field(description="Start character index in original document")
    end_index: int = Field(description="End character index in original document")
    start_line: int = Field(
        default=0, description="1-based line number in the original document"
    )
    end_line: int = Field(
        default=0, description="1-based line number in the original document"
    )
    index: int | None = Field(
        default=None,
        description="Zero-based chunk index within the document when available",
    )

    @classmethod
    def of(cls, chunk: Chunk, index: int | None = None) -> ChunkData:
        """The data of a chunk an `exar.ingest.Chunker` made."""
        return cls(
            text=chunk.text,
            token_count=chunk.tokens,
            start_index=chunk.start,
            end_index=chunk.end,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            index=index,
        )


type EntryKind = Literal[
    "user_markdown",
    "image",
    "video",
    "convertible",
    "binary_stub",
]
"""Logical entry kinds stored in document metadata."""

type EntryOrigin = Literal["upload", "collection", "extracted", "imported", "synced"]
"""Origin of a logical entry.

``imported`` marks an entry the filesystem reconciler discovered on disk
without a prior SQL row (a hand-dropped file today, and the output of the
future read-write shell tool's fold-back).  Its real provenance is not
recoverable from disk, so it is stamped imported rather than guessed.
``synced`` marks an entry an external source's sync mirrored.
"""

type EntryGeneratedBy = Literal["user", "converter", "vision", "stub"]
"""How the markdown representation of an entry was produced."""


class EntryMetadata(BaseModel):
    """Filesystem metadata for a logical stem entry."""

    entry_kind: EntryKind = Field(
        default="user_markdown",
        description="Logical entry kind for the chunked markdown",
    )
    stem_path: str = Field(
        description="Workspace-relative logical stem path for the entry",
    )
    description_path: str = Field(
        description="Workspace-relative markdown path for the entry",
    )
    original_path: str | None = Field(
        default=None,
        description="Workspace-relative original file path for the entry",
    )
    assets_dir: str | None = Field(
        default=None,
        description="Workspace-relative child-assets directory for the entry",
    )
    origin: EntryOrigin = Field(
        default="upload",
        description="How the logical entry was created",
    )
    generated_by: EntryGeneratedBy = Field(
        default="user",
        description="How the markdown content was produced",
    )


class DocumentMetadata(EntryMetadata):
    """Metadata for a processed logical entry."""

    id: UUID = Field(description="The document's row id")
    pipeline: str = Field(description="The chunking pipeline used")
    created_at: datetime = Field(description="When the metadata was created")
    size_bytes: int | None = Field(
        default=None,
        description="On-disk byte size of the chunked description, when known",
    )
    chunks: list[ChunkData] = Field(description="The document chunks")
    mime: str | None = Field(
        default=None,
        description="Media type the original's name declares, else the description's",
    )
