"""Table-aware document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache

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


@lru_cache(maxsize=4)
def _build_chunker(config_json: str) -> TableChunker:
    config = TableChunkerConfig.model_validate_json(config_json)
    return TableChunker(
        tokenizer=config.tokenizer,
        chunk_size=config.chunk_size,
    )


@dataclass(slots=True, frozen=True)
class TableDocumentChunker(DocumentChunker):
    """Chunker that splits tabular text into row-based chunks.

    Uses chonkie's TableChunker for table-aware splitting.
    Best suited for CSV and markdown table data.
    """

    name = "table"
    label = "Table"
    description = "Row-based splitting for tabular data"
    config: TableChunkerConfig = field(default_factory=TableChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = _build_chunker(self.config.model_dump_json()).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text into table-aware chunks."""
        return await asyncio.to_thread(self._chunk, text)
