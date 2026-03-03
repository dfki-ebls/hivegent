"""LLM-guided document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import SlumberChunker
from chonkie.genie.openai import OpenAIGenie
from pydantic import Field

from .base import BaseChonkieConfig, ChunkData, DocumentChunker, apply_chonkie

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

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text using LLM-guided chunking.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        from ..config import settings

        parsed = SlumberChunkerConfig(**(config or {}))
        genie = OpenAIGenie(
            model=settings.llm.small_model or settings.llm.model,
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url or None,
        )
        chunks = SlumberChunker(
            genie=genie,
            chunk_size=parsed.chunk_size,
            candidate_size=parsed.candidate_size,
            min_characters_per_chunk=parsed.min_characters_per_chunk,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)
