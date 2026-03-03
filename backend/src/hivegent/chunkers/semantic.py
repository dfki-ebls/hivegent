"""Semantic document chunker using chonkie."""

import asyncio
from dataclasses import dataclass
from typing import Any

from chonkie import SemanticChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["SemanticChunkerConfig", "SemanticDocumentChunker"]

_DEFAULT_EMBEDDING_MODEL = "minishlab/potion-base-32M"


class SemanticChunkerConfig(BaseChonkieConfig):
    """Configuration for the Semantic chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    threshold: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="Similarity threshold for chunk boundaries.",
    )
    similarity_window: int = Field(
        default=3,
        ge=1,
        description="Number of sentences to compare for similarity.",
    )
    min_sentences_per_chunk: int = Field(
        default=1,
        ge=1,
        description="Minimum number of sentences per chunk.",
    )
    min_characters_per_sentence: int = Field(
        default=24,
        ge=1,
        description="Minimum character count for a text span to be considered a sentence.",
    )


@dataclass(slots=True, frozen=True)
class SemanticDocumentChunker(DocumentChunker):
    """Chunker that splits text based on semantic similarity.

    Uses chonkie's SemanticChunker with a lightweight embedding model.
    Best suited for documents where topical coherence within chunks matters.
    Requires ``chonkie[semantic]`` (model2vec).
    """

    name = "semantic"

    def _chunk(self, text: str, config: dict[str, Any] | None) -> list[ChunkData]:
        parsed = SemanticChunkerConfig(**(config or {}))
        chunks = SemanticChunker(
            embedding_model=_DEFAULT_EMBEDDING_MODEL,
            threshold=parsed.threshold,
            chunk_size=parsed.chunk_size,
            similarity_window=parsed.similarity_window,
            min_sentences_per_chunk=parsed.min_sentences_per_chunk,
            min_characters_per_sentence=parsed.min_characters_per_sentence,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)

    async def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text using semantic similarity boundaries.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        return await asyncio.to_thread(self._chunk, text, config)
