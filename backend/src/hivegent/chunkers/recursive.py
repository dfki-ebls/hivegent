"""Recursive document chunker using chonkie."""

from dataclasses import dataclass

from chonkie import RecursiveChunker

from .base import ChunkData, DocumentChunker

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

    def __call__(self, text: str, /) -> list[ChunkData]:
        """Split text using hierarchical recursive splitting.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        chunks = RecursiveChunker(chunk_size=self.chunk_size).chunk(text)
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
