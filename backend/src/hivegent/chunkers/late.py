"""Late-interaction document chunker using chonkie."""

from dataclasses import dataclass, field
from functools import lru_cache

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


@lru_cache(maxsize=2)
def _build_chunker(config_json: str, device: str) -> LateChunker:
    config = LateChunkerConfig.model_validate_json(config_json)
    # ``device`` forwards through LateChunker's kwargs to the underlying
    # SentenceTransformer (``None`` lets it self-detect via the process env).
    return LateChunker(
        embedding_model=_DEFAULT_EMBEDDING_MODEL,
        chunk_size=config.chunk_size,
        min_characters_per_chunk=config.min_characters_per_chunk,
        device=None if device == "auto" else device,
    )


@dataclass(slots=True, frozen=True)
class LateDocumentChunker(DocumentChunker):
    """Chunker that uses late-interaction embeddings for chunking.

    Uses chonkie's LateChunker with a ModernBERT embedding model.
    Best suited for documents needing embedding-aware chunk boundaries.
    """

    name = "late"
    label = "Late"
    description = "Late-interaction embedding-aware chunk boundaries"
    config: LateChunkerConfig = field(default_factory=LateChunkerConfig)
    device: str = field(default="auto", kw_only=True)
    """Compute device for the model (``"auto"`` self-detects); code-level, not a setting."""

    def _split_sync(self, text: str) -> list[ChunkData]:
        chunks = _build_chunker(self.config.model_dump_json(), self.device).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)
