"""Recursive document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import RecursiveChunker

from .base import ChunkData, DocumentChunker
from .config import RecursiveChunkerConfig

__all__ = ["RecursiveDocumentChunker"]


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
