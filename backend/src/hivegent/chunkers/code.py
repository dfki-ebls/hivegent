"""Code-aware document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field

from chonkie import CodeChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["CodeChunkerConfig", "CodeDocumentChunker"]


class CodeChunkerConfig(BaseChonkieConfig):
    """Configuration for the Code chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    language: str = Field(
        default="auto",
        description="Programming language for parsing (e.g. 'python', 'auto').",
    )
    include_nodes: bool = Field(
        default=False,
        description="Whether to include tree-sitter node information in chunks.",
    )


@dataclass(slots=True, frozen=True)
class CodeDocumentChunker(DocumentChunker):
    """Chunker that splits source code respecting syntax boundaries.

    Uses chonkie's CodeChunker with tree-sitter for AST-aware splitting.
    Best suited for source code files.
    Requires ``chonkie[code]`` (tree-sitter-language-pack).
    """

    name = "code"
    config: CodeChunkerConfig = field(default_factory=CodeChunkerConfig)

    def _chunk(self, text: str) -> list[ChunkData]:
        chunks = CodeChunker(
            chunk_size=self.config.chunk_size,
            language=self.config.language,
            include_nodes=self.config.include_nodes,
        ).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split code using syntax-aware boundaries."""
        return await asyncio.to_thread(self._chunk, text)
