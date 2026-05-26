"""Fast delimiter-based document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache

from chonkie import FastChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["FastChunkerConfig", "FastDocumentChunker"]


class FastChunkerConfig(BaseChonkieConfig):
    """Configuration for the Fast chunking pipeline."""

    chunk_size: int = Field(
        default=4096,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    delimiters: str = Field(
        default=r"\n.?",
        description="Regex pattern for delimiters.",
    )
    prefix: bool = Field(
        default=False,
        description="Whether to include delimiter as prefix of next chunk.",
    )
    consecutive: bool = Field(
        default=False,
        description="Whether to merge consecutive delimiters.",
    )


@lru_cache(maxsize=4)
def _build_chunker(config_json: str) -> FastChunker:
    config = FastChunkerConfig.model_validate_json(config_json)
    return FastChunker(
        chunk_size=config.chunk_size,
        delimiters=config.delimiters,
        prefix=config.prefix,
        consecutive=config.consecutive,
    )


@dataclass(slots=True, frozen=True)
class FastDocumentChunker(DocumentChunker):
    """Chunker that splits text using fast delimiter-based splitting.

    Uses chonkie's FastChunker for high-throughput chunking.
    Best suited for large documents where speed is critical.
    """

    name = "fast"
    label = "Fast"
    description = "High-throughput delimiter-based splitting"
    config: FastChunkerConfig = field(default_factory=FastChunkerConfig)

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
        """Split text using fast delimiter-based chunking."""
        return await asyncio.to_thread(self._chunk, text)
