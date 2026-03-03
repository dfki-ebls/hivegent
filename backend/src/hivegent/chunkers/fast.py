"""Fast delimiter-based document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import FastChunker
from pydantic import Field

from .base import BaseChonkieConfig, ChunkData, DocumentChunker, apply_chonkie

__all__ = ["FastChunkerConfig", "FastDocumentChunker"]


class FastChunkerConfig(BaseChonkieConfig):
    """Configuration for the Fast chunking pipeline."""

    chunk_size: int = Field(
        default=4096,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    delimiters: str = Field(
        default=r"\n.?",
        description="Regex pattern for delimiters.",
    )
    prefix: bool = Field(
        default=False,
        description="Whether to include delimiter as prefix of next chunk.",
    )
    consecutive: bool = Field(
        default=False,
        description="Whether to merge consecutive delimiters.",
    )


@dataclass(slots=True, frozen=True)
class FastDocumentChunker(DocumentChunker):
    """Chunker that splits text using fast delimiter-based splitting.

    Uses chonkie's FastChunker for high-throughput chunking.
    Best suited for large documents where speed is critical.
    """

    name = "fast"

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text using fast delimiter-based chunking.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        parsed = FastChunkerConfig(**(config or {}))
        chunks = FastChunker(
            chunk_size=parsed.chunk_size,
            delimiters=parsed.delimiters,
            prefix=parsed.prefix,
            consecutive=parsed.consecutive,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)
