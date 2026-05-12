"""Semantic document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field

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
    config: SemanticChunkerConfig = field(default_factory=SemanticChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = SemanticChunker(
            embedding_model=_DEFAULT_EMBEDDING_MODEL,
            threshold=self.config.threshold,
            chunk_size=self.config.chunk_size,
            similarity_window=self.config.similarity_window,
            min_sentences_per_chunk=self.config.min_sentences_per_chunk,
            min_characters_per_sentence=self.config.min_characters_per_sentence,
        ).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text using semantic similarity boundaries."""
        return await asyncio.to_thread(self._chunk, text)
