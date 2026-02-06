"""Sentence-based document chunker using chonkie."""

from chonkie import SentenceChunker

from .base import ChunkData, DocumentChunker

__all__ = ["SentenceDocumentChunker"]


class SentenceDocumentChunker(DocumentChunker):
    """Chunker that splits text respecting sentence boundaries.

    Uses chonkie's SentenceChunker to keep sentences intact.
    Best suited for prose and plain text documents.
    """

    def __init__(self, chunk_size: int = 2048) -> None:
        """Initialize the sentence chunker.

        Args:
            chunk_size: Target chunk size in tokens.
        """
        super().__init__(chunk_size)
        self._chunker = SentenceChunker(chunk_size=chunk_size)

    @property
    def name(self) -> str:
        """The unique name of this chunker."""
        return "sentence"

    def chunk(self, text: str) -> list[ChunkData]:
        """Split text into sentence-boundary-respecting chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        chunks = self._chunker.chunk(text)
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
