"""Late-interaction document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field

from chonkie import LateChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

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
    config: LateChunkerConfig = field(default_factory=LateChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = LateChunker(
            embedding_model=_DEFAULT_EMBEDDING_MODEL,
            chunk_size=self.config.chunk_size,
            min_characters_per_chunk=self.config.min_characters_per_chunk,
        ).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text using late-interaction chunking."""
        return await asyncio.to_thread(self._chunk, text)
