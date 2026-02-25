"""Pydantic configuration models for document chunkers.

Fields and defaults match the ``chonkie`` library constructors.
"""

from pydantic import BaseModel, Field

__all__ = [
    "RecursiveChunkerConfig",
    "SentenceChunkerConfig",
    "TokenChunkerConfig",
]


class TokenChunkerConfig(BaseModel):
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


class SentenceChunkerConfig(BaseModel):
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


class RecursiveChunkerConfig(BaseModel):
    """Configuration for the Recursive chunking pipeline."""

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
