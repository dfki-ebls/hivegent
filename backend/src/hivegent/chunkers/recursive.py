"""Recursive document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import RecursiveChunker
from pydantic import BaseModel, Field

from .base import ChunkData, DocumentChunker

__all__ = ["RecursiveChunkerConfig", "RecursiveDocumentChunker"]


class RecursiveChunkerConfig(BaseModel):
    """Configuration for the Recursive chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    min_characters_per_chunk: int = Field(
        default=24,
        ge=1,
        description="Minimum character count for a chunk.",
    )


@dataclass(slots=True, frozen=True)
class RecursiveDocumentChunker(DocumentChunker):
    """Chunker that splits text hierarchically.

    Uses chonkie's RecursiveChunker for structured splitting by headings,
    paragraphs, and sentences.
    Best suited for markdown and other structured documents.
    """

    name = "recursive"
    chunk_size = 2048

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text using hierarchical recursive splitting.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        parsed = RecursiveChunkerConfig(**(config or {}))
        chunks = RecursiveChunker(
            chunk_size=parsed.chunk_size,
            min_characters_per_chunk=parsed.min_characters_per_chunk,
        ).chunk(text)
        return [
            ChunkData(
                text=c.text,
                token_count=c.token_count,
                start_index=c.start_index,
                end_index=c.end_index,
                index=i,
            )
            for i, c in enumerate(chunks)
        ]
