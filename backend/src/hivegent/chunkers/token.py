"""Token-based document chunker using chonkie."""

from dataclasses import dataclass

from chonkie import TokenChunker

from .base import ChunkData, DocumentChunker

__all__ = ["TokenDocumentChunker"]


@dataclass(slots=True, frozen=True)
class TokenDocumentChunker(DocumentChunker):
    """Chunker that splits text into fixed token-count chunks.

    Uses chonkie's TokenChunker for uniform chunk sizes.
    Best suited for tabular or structured data like CSV.
    """

    name = "token"
    chunk_size = 2048

    def __call__(self, text: str, /) -> list[ChunkData]:
        """Split text into fixed token-count chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        chunks = TokenChunker(chunk_size=self.chunk_size).chunk(text)
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
