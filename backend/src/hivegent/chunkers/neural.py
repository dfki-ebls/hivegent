"""Neural document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field

from chonkie import NeuralChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["NeuralChunkerConfig", "NeuralDocumentChunker"]

_DEFAULT_MODEL = "mirth/chonky_distilbert_base_uncased_1"


class NeuralChunkerConfig(BaseChonkieConfig):
    """Configuration for the Neural chunking pipeline."""

    min_characters_per_chunk: int = Field(
        default=24,
        ge=1,
        description="Minimum character count for a chunk.",
    )
    device_map: str = Field(
        default="auto",
        description="Device to run the model on (e.g. 'auto', 'cpu', 'cuda', 'mps').",
    )


@dataclass(slots=True, frozen=True)
class NeuralDocumentChunker(DocumentChunker):
    """Chunker that uses a neural model to find chunk boundaries.

    Uses chonkie's NeuralChunker with a fine-tuned DistilBERT model.
    Best suited for documents where semantic boundaries matter.
    """

    name = "neural"
    label = "Neural"
    description = "Neural model-based chunk boundary detection"
    config: NeuralChunkerConfig = field(default_factory=NeuralChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = NeuralChunker(
            model=_DEFAULT_MODEL,
            device_map=self.config.device_map,
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
        """Split text using neural chunk boundary detection."""
        return await asyncio.to_thread(self._chunk, text)
