"""Code-aware document chunker using chonkie."""

import asyncio
from dataclasses import dataclass
from typing import Any

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

    def _chunk(self, text: str, config: dict[str, Any] | None) -> list[ChunkData]:
        parsed = CodeChunkerConfig(**(config or {}))
        chunks = CodeChunker(
            chunk_size=parsed.chunk_size,
            language=parsed.language,
            include_nodes=parsed.include_nodes,
        ).chunk(text)
        return apply_chonkie(chunks, parsed.refineries)

    async def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split code using syntax-aware boundaries.

        Args:
            text: The source code text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects.
        """
        return await asyncio.to_thread(self._chunk, text, config)
