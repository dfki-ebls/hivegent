"""Sentence-based document chunker using chonkie."""

from dataclasses import dataclass

from chonkie import SentenceChunker

from .base import ChunkData, DocumentChunker

__all__ = ["SentenceDocumentChunker"]


@dataclass(slots=True, frozen=True)
class SentenceDocumentChunker(DocumentChunker):
    """Chunker that splits text respecting sentence boundaries.

    Uses chonkie's SentenceChunker to keep sentences intact.
    Best suited for prose and plain text documents.
    """

    name = "sentence"
    chunk_size = 2048

    def __call__(self, text: str, /) -> list[ChunkData]:
        """Split text into sentence-boundary-respecting chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        chunks = SentenceChunker(chunk_size=self.chunk_size).chunk(text)
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
