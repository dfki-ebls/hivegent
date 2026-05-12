"""LLM-guided document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field

from chonkie import SlumberChunker
from chonkie.genie.openai import OpenAIGenie
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["SlumberChunkerConfig", "SlumberDocumentChunker"]


class SlumberChunkerConfig(BaseChonkieConfig):
    """Configuration for the Slumber chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    candidate_size: int = Field(
        default=512,
        ge=64,
        description="Candidate window size for the LLM to evaluate.",
    )
    min_characters_per_chunk: int = Field(
        default=24,
        ge=1,
        description="Minimum character count for a chunk.",
    )


@dataclass(slots=True, frozen=True)
class SlumberDocumentChunker(DocumentChunker):
    """Chunker that uses an LLM to guide chunk boundary decisions.

    Uses chonkie's SlumberChunker with an OpenAI-compatible model.
    Best suited for documents needing intelligent, context-aware splitting.
    """

    name = "slumber"
    config: SlumberChunkerConfig = field(default_factory=SlumberChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        from ..config import settings

        genie = OpenAIGenie(
            model=settings.llm.aux_model or settings.llm.model,
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url or None,
        )
        chunks = SlumberChunker(
            genie=genie,
            chunk_size=self.config.chunk_size,
            candidate_size=self.config.candidate_size,
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
        """Split text using LLM-guided chunking."""
        return await asyncio.to_thread(self._chunk, text)
