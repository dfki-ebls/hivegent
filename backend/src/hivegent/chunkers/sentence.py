"""Sentence-based document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import SentenceChunker

from .base import ChunkData, DocumentChunker
from .config import SentenceChunkerConfig

__all__ = ["SentenceDocumentChunker"]


@dataclass(slots=True, frozen=True)
class SentenceDocumentChunker(DocumentChunker):
    """Chunker that splits text respecting sentence boundaries.

    Uses chonkie's SentenceChunker to keep sentences intact.
    Best suited for prose and plain text documents.
    """

    name = "sentence"
    chunk_size = 2048

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text into sentence-boundary-respecting chunks.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        parsed = SentenceChunkerConfig(**(config or {}))
        chunks = SentenceChunker(
            chunk_size=parsed.chunk_size,
            chunk_overlap=parsed.chunk_overlap,
            min_sentences_per_chunk=parsed.min_sentences_per_chunk,
            min_characters_per_sentence=parsed.min_characters_per_sentence,
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
