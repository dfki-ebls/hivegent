"""Recursive document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache

from chonkie import RecursiveChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["RecursiveChunkerConfig", "RecursiveDocumentChunker"]


class RecursiveChunkerConfig(BaseChonkieConfig):
    """Configuration for the Recursive chunking pipeline."""

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


@lru_cache(maxsize=4)
def _build_chunker(config_json: str) -> RecursiveChunker:
    config = RecursiveChunkerConfig.model_validate_json(config_json)
    return RecursiveChunker(
        chunk_size=config.chunk_size,
        min_characters_per_chunk=config.min_characters_per_chunk,
    )


@dataclass(slots=True, frozen=True)
class RecursiveDocumentChunker(DocumentChunker):
    """Chunker that splits text hierarchically.

    Uses chonkie's RecursiveChunker for structured splitting by headings,
    paragraphs, and sentences.
    Best suited for markdown and other structured documents.
    """

    name = "recursive"
    label = "Recursive"
    description = "Hierarchical splitting by headings, paragraphs, and sentences"
    config: RecursiveChunkerConfig = field(default_factory=RecursiveChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = _build_chunker(self.config.model_dump_json()).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text using hierarchical recursive splitting."""
        return await asyncio.to_thread(self._chunk, text)
