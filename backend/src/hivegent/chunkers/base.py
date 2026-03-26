"""Base chunking types and interfaces."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ChunkData",
    "ChunkSummary",
    "DocumentChunker",
    "EntryGeneratedBy",
    "EntryKind",
    "EntryMetadata",
    "EntryOrigin",
    "DocumentMetadata",
    "RetrievedChunk",
]


class ChunkData(BaseModel):
    """A single chunk used by chunkers and persisted chunk metadata."""

    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    start_index: int = Field(description="Start character index in original document")
    end_index: int = Field(description="End character index in original document")
    index: int | None = Field(
        default=None,
        description="Zero-based chunk index within the document when available",
    )


type EntryKind = Literal[
    "user_markdown",
    "image",
    "convertible",
    "binary_stub",
]
"""Logical entry kinds stored in document metadata."""

type EntryOrigin = Literal["upload", "collection", "extracted"]
"""Origin of a logical entry."""

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
    mime: str | None = Field(
        default=None,
        description="Detected MIME type for the original file when available",
    )
    origin: EntryOrigin = Field(
        default="upload",
        description="How the logical entry was created",
    )
    generated_by: EntryGeneratedBy = Field(
        default="user",
        description="How the markdown content was produced",
    )
    files: list[str] = Field(
        default_factory=list,
        description="Workspace-relative files that belong to the logical entry",
    )


class DocumentMetadata(EntryMetadata):
    """Metadata for a processed logical entry."""

    pipeline: str = Field(description="The chunking pipeline used")
    created_at: datetime = Field(description="When the metadata was created")
    chunks: list[ChunkData] = Field(description="The document chunks")


class ChunkSummary(BaseModel):
    """Summary metadata for a single chunk used by agent tools."""

    token_count: int = Field(description="Number of tokens in the chunk")
    start_index: int = Field(description="Start character index in original document")
    end_index: int = Field(description="End character index in original document")


class RetrievedChunk(BaseModel):
    """A chunk retrieved from search."""

    store_key: str | None = Field(
        default=None,
        description="Source store identifier (e.g. 'user:alice', 'group:eng')",
    )
    filename: str = Field(description="The document filename")
    chunk_index: int = Field(description="Chunk index within the document")
    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    score: float = Field(description="The relevance score")
    image_path: str | None = Field(
        default=None,
        description="Workspace-relative path to the original image file when this chunk describes an image",
    )


class DocumentChunker(ABC):
    """Abstract base class for document chunkers.

    All chunkers must inherit from this class and implement the required
    properties and methods.
    Chunkers split document text into smaller pieces for retrieval.
    """

    name: ClassVar[str]

    @abstractmethod
    async def __call__(
        self,
        text: str,
        /,
    ) -> list[ChunkData]:
        """Split text into chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        ...
