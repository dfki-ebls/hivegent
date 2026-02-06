"""Recursive document chunker using chonkie."""

from chonkie import RecursiveChunker

from .base import ChunkData, DocumentChunker

__all__ = ["RecursiveDocumentChunker"]


class RecursiveDocumentChunker(DocumentChunker):
    """Chunker that splits text hierarchically.

    Uses chonkie's RecursiveChunker for structured splitting by headings,
    paragraphs, and sentences.
    Best suited for markdown and other structured documents.
    """

    def __init__(self, chunk_size: int = 2048) -> None:
        """Initialize the recursive chunker.

        Args:
            chunk_size: Target chunk size in tokens.
        """
        super().__init__(chunk_size)
        self._chunker = RecursiveChunker(chunk_size=chunk_size)

    @property
    def name(self) -> str:
        """The unique name of this chunker."""
        return "recursive"

    def chunk(self, text: str) -> list[ChunkData]:
        """Split text using hierarchical recursive splitting.

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
