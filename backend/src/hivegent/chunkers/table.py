"""Table-aware document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field

from chonkie import TableChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

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
    config: TableChunkerConfig = field(default_factory=TableChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = TableChunker(
            tokenizer=self.config.tokenizer,
            chunk_size=self.config.chunk_size,
        ).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def __call__(
        self,
        text: str,
        /,
    ) -> list[ChunkData]:
        """Split text into table-aware chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        return await asyncio.to_thread(self._chunk, text)
