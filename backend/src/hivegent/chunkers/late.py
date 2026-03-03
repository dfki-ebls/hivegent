"""Late-interaction document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import LateChunker
from pydantic import Field

from .base import BaseChonkieConfig, ChunkData, DocumentChunker, apply_chonkie

__all__ = ["LateChunkerConfig", "LateDocumentChunker"]

_DEFAULT_EMBEDDING_MODEL = "nomic-ai/modernbert-embed-base"


class LateChunkerConfig(BaseChonkieConfig):
    """Configuration for the Late chunking pipeline."""

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
class LateDocumentChunker(DocumentChunker):
    """Chunker that uses late-interaction embeddings for chunking.

    Uses chonkie's LateChunker with a ModernBERT embedding model.
    Best suited for documents needing embedding-aware chunk boundaries.
    """

    name = "late"

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text using late-interaction chunking.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        parsed = LateChunkerConfig(**(config or {}))
        chunks = LateChunker(
            embedding_model=_DEFAULT_EMBEDDING_MODEL,
            chunk_size=parsed.chunk_size,
            min_characters_per_chunk=parsed.min_characters_per_chunk,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)
