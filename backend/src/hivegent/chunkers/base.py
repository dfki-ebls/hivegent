"""Base chunking types and interfaces."""

import bisect
from abc import ABC
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from ..workers.pool import run_offloaded

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
    "video",
    "convertible",
    "binary_stub",
]
"""Logical entry kinds stored in document metadata."""

type EntryOrigin = Literal["upload", "collection", "extracted", "imported"]
"""Origin of a logical entry.

``imported`` marks an entry the filesystem reconciler discovered on disk
without a prior SQL row — a hand-dropped file today, and the output of the
future read-write shell tool's fold-back.  Its real provenance is not
recoverable from disk, so it is stamped imported rather than guessed.
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

    id: str = Field(description="The document's row id")
    pipeline: str = Field(description="The chunking pipeline used")
    created_at: datetime = Field(description="When the metadata was created")
    size_bytes: int | None = Field(
        default=None,
        description="On-disk byte size of the chunked description, when known",
    )
    chunks: list[ChunkData] = Field(description="The document chunks")
    content_digest: str | None = Field(
        default=None,
        description=(
            "Full SHA-256 digest of the chunked markdown. Lets the reconciler "
            "and the future shell fold-back skip re-indexing an entry whose "
            "on-disk bytes are unchanged."
        ),
    )


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

    CPU/model-bound chunkers implement the sync :meth:`_split_sync`; the base
    :meth:`_split` offloads it through :func:`hivegent.workers.pool.run_offloaded`
    (a persistent worker process when the pool is on, a lock-guarded thread
    otherwise).  Chunkers that do no offloadable work (the no-op chunker, the
    LLM-driven slumber chunker) override :meth:`_split` directly.  The base
    :meth:`__call__` annotates each returned chunk with 1-based line numbers.
    """

    name: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]

    def _split_sync(self, text: str, /) -> list[ChunkData]:
        """CPU-bound chunking core, run in a worker process.

        CPU- and model-bound chunkers implement this; the base :meth:`_split`
        offloads it to the process pool.  Chunkers that override :meth:`_split`
        never reach here.  (Mirrors :meth:`DocumentConverter._convert_sync`.)

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects (line numbers may be unset).
        """
        raise NotImplementedError

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text into chunks.

        Defaults to offloading :meth:`_split_sync` off the event loop (a worker
        process when the pool is on, a lock-guarded thread otherwise).  Chunkers
        that are trivial or I/O-bound override this to run on the event loop.

        Args:
            text: The document text to chunk.
            mime: Detected MIME type of the original file, when available.
                Format-aware chunkers may use this to switch strategies.

        Returns:
            List of ChunkData objects (line numbers may be unset).
        """
        return await run_offloaded(self._split_sync, text)

    async def __call__(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text into chunks and annotate line numbers.

        Args:
            text: The document text to chunk.
            mime: Detected MIME type of the original file, when available.

        Returns:
            List of ChunkData objects with 1-based line numbers set.
        """
        chunks = await self._split(text, mime=mime)
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
