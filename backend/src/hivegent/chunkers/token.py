"""Token-based document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import TokenChunker
from pydantic import BaseModel, Field

from .base import ChunkData, DocumentChunker

__all__ = ["TokenChunkerConfig", "TokenDocumentChunker"]


class TokenChunkerConfig(BaseModel):
    """Configuration for the Token chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    chunk_overlap: int = Field(
        default=0,
        ge=0,
        description="Number of overlapping tokens between consecutive chunks.",
    )


@dataclass(slots=True, frozen=True)
class TokenDocumentChunker(DocumentChunker):
    """Chunker that splits text into fixed token-count chunks.

    Uses chonkie's TokenChunker for uniform chunk sizes.
    Best suited for tabular or structured data like CSV.
    """

    name = "token"
    chunk_size = 2048

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text into fixed token-count chunks.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        parsed = TokenChunkerConfig(**(config or {}))
        chunks = TokenChunker(
            chunk_size=parsed.chunk_size,
            chunk_overlap=parsed.chunk_overlap,
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
