"""Token-based document chunker using chonkie."""

from dataclasses import dataclass, field
from functools import lru_cache

from chonkie import TokenChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["TokenChunkerConfig", "TokenDocumentChunker"]


class TokenChunkerConfig(BaseChonkieConfig):
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


@lru_cache(maxsize=4)
def _build_chunker(config_json: str) -> TokenChunker:
    config = TokenChunkerConfig.model_validate_json(config_json)
    return TokenChunker(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )


@dataclass(slots=True, frozen=True)
class TokenDocumentChunker(DocumentChunker):
    """Chunker that splits text into fixed token-count chunks.

    Uses chonkie's TokenChunker for uniform chunk sizes.
    Best suited for tabular or structured data like CSV.
    """

    name = "token"
    config: TokenChunkerConfig = field(default_factory=TokenChunkerConfig)

    def _split_sync(self, text: str) -> list[ChunkData]:
        chunks = _build_chunker(self.config.model_dump_json()).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)
