"""Base chunking types and interfaces."""

from __future__ import annotations

import bisect
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ChunkData",
    "ChunkSummary",
    "DocumentChunker",
    "DocumentMetadata",
    "EntryGeneratedBy",
    "EntryKind",
    "EntryMetadata",
    "EntryOrigin",
    "RetrievedChunk",
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
    start_line: int = Field(
        description="1-based start line number in the original document"
    )
    end_line: int = Field(
        description="1-based end line number in the original document"
    )


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
    start_line: int = Field(
        description="1-based start line number in the original document"
    )
    end_line: int = Field(
        description="1-based end line number in the original document"
    )
    start_index: int = Field(
        description="Start character index in the original document (for frontend highlighting only)",
    )
    end_index: int = Field(
        description="End character index in the original document (for frontend highlighting only)",
    )
    image_path: str | None = Field(
        default=None,
        description="Workspace-relative path to the original image file when this chunk describes an image",
    )


class DocumentChunker(ABC):
    """Abstract base class for document chunkers.

    Subclasses implement :meth:`_split` to produce chunks with character
    offsets.  The base :meth:`__call__` automatically annotates each chunk
    with 1-based line numbers derived from those offsets.
    """

    name: ClassVar[str]

    @abstractmethod
    async def _split(
        self,
        text: str,
        /,
    ) -> list[ChunkData]:
        """Split text into chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects (line numbers may be unset).
        """
        ...

    async def __call__(
        self,
        text: str,
        /,
    ) -> list[ChunkData]:
        """Split text into chunks and annotate line numbers.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects with 1-based line numbers set.
        """
        chunks = await self._split(text)
        if not chunks:
            return []
        line_starts = [0]
        pos = text.find("\n")
        while pos != -1:
            line_starts.append(pos + 1)
            pos = text.find("\n", pos + 1)
        return [
            chunk.model_copy(
                update={
                    "start_line": bisect.bisect_right(line_starts, chunk.start_index),
                    "end_line": bisect.bisect_right(
                        line_starts, max(chunk.end_index - 1, chunk.start_index)
                    ),
                }
            )
            for chunk in chunks
        ]
