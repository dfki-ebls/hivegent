"""Sentence-based document chunker using chonkie."""

from dataclasses import dataclass, field
from functools import lru_cache

from chonkie import SentenceChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["SentenceChunkerConfig", "SentenceDocumentChunker"]


class SentenceChunkerConfig(BaseChonkieConfig):
    """Configuration for the Sentence chunking pipeline."""

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
    min_sentences_per_chunk: int = Field(
        default=1,
        ge=1,
        description="Minimum number of sentences per chunk.",
    )
    min_characters_per_sentence: int = Field(
        default=12,
        ge=1,
        description="Minimum character count for a text span to be considered a sentence.",
    )


@lru_cache(maxsize=4)
def _build_chunker(config_json: str) -> SentenceChunker:
    config = SentenceChunkerConfig.model_validate_json(config_json)
    return SentenceChunker(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        min_sentences_per_chunk=config.min_sentences_per_chunk,
        min_characters_per_sentence=config.min_characters_per_sentence,
    )


@dataclass(slots=True, frozen=True)
class SentenceDocumentChunker(DocumentChunker):
    """Chunker that splits text respecting sentence boundaries.

    Uses chonkie's SentenceChunker to keep sentences intact.
    Best suited for prose and plain text documents.
    """

    name = "sentence"
    config: SentenceChunkerConfig = field(default_factory=SentenceChunkerConfig)

    def _split_sync(self, text: str) -> list[ChunkData]:
        chunks = _build_chunker(self.config.model_dump_json()).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)
