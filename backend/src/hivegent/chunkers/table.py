"""Table-aware document chunker using chonkie."""

from dataclasses import dataclass
from typing import Any

from chonkie import TableChunker
from pydantic import Field

from .base import BaseChonkieConfig, ChunkData, DocumentChunker, apply_chonkie

__all__ = ["TableChunkerConfig", "TableDocumentChunker"]


class TableChunkerConfig(BaseChonkieConfig):
    """Configuration for the Table chunking pipeline."""

    chunk_size: int = Field(
        default=3,
        ge=1,
        le=32768,
        description="Target chunk size in rows.",
    )
    tokenizer: str = Field(
        default="row",
        description="Tokenizer to use for counting (e.g. 'row').",
    )


@dataclass(slots=True, frozen=True)
class TableDocumentChunker(DocumentChunker):
    """Chunker that splits tabular text into row-based chunks.

    Uses chonkie's TableChunker for table-aware splitting.
    Best suited for CSV and markdown table data.
    """

    name = "table"

    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text into table-aware chunks.

        Args:
            text: The document text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        parsed = TableChunkerConfig(**(config or {}))
        chunks = TableChunker(
            tokenizer=parsed.tokenizer,
            chunk_size=parsed.chunk_size,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)
