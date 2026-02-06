"""Token-based document chunker using chonkie."""

from chonkie import TokenChunker

from .base import ChunkData, DocumentChunker

__all__ = ["TokenDocumentChunker"]


class TokenDocumentChunker(DocumentChunker):
    """Chunker that splits text into fixed token-count chunks.

    Uses chonkie's TokenChunker for uniform chunk sizes.
    Best suited for tabular or structured data like CSV.
    """

    def __init__(self, chunk_size: int = 2048) -> None:
        """Initialize the token chunker.

        Args:
            chunk_size: Target chunk size in tokens.
        """
        super().__init__(chunk_size)
        self._chunker = TokenChunker(chunk_size=chunk_size)

    @property
    def name(self) -> str:
        """The unique name of this chunker."""
        return "token"

    def chunk(self, text: str) -> list[ChunkData]:
        """Split text into fixed token-count chunks.

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
