"""Base chunking types and interfaces."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field

__all__ = [
    "ChunkData",
    "ChunkSummary",
    "DocumentChunker",
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


class DocumentMetadata(BaseModel):
    """Metadata for a processed document, including chunks and companion images."""

    pipeline: str = Field(description="The chunking pipeline used")
    created_at: datetime = Field(description="When the metadata was created")
    chunks: list[ChunkData] = Field(description="The document chunks")
    images: list[str] = Field(
        default_factory=list,
        description="Workspace-relative paths of companion images",
    )


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
