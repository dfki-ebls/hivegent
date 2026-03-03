"""Neural document chunker using chonkie."""

import asyncio
from dataclasses import dataclass
from typing import Any

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
        default="cpu",
        description="Device to run the model on (e.g. 'cpu', 'cuda').",
    )


@dataclass(slots=True, frozen=True)
class NeuralDocumentChunker(DocumentChunker):
    """Chunker that uses a neural model to find chunk boundaries.

    Uses chonkie's NeuralChunker with a fine-tuned DistilBERT model.
    Best suited for documents where semantic boundaries matter.
    """

    name = "neural"

    def _chunk(self, text: str, config: dict[str, Any] | None) -> list[ChunkData]:
        parsed = NeuralChunkerConfig(**(config or {}))
        chunks = NeuralChunker(
            model=_DEFAULT_MODEL,
            device_map=parsed.device_map,
            min_characters_per_chunk=parsed.min_characters_per_chunk,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)

    async def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text using neural chunk boundary detection.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        return await asyncio.to_thread(self._chunk, text, config)
