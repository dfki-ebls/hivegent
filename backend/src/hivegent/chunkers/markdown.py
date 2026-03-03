"""Markdown-aware document chunker using chonkie."""

import asyncio
from dataclasses import dataclass
from typing import Any

from chonkie import MarkdownChef
from chonkie.tokenizer import AutoTokenizer
from chonkie.types.base import Chunk

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["MarkdownChunkerConfig", "MarkdownDocumentChunker"]


class MarkdownChunkerConfig(BaseChonkieConfig):
    """Configuration for the Markdown chunking pipeline."""


@dataclass(slots=True, frozen=True)
class MarkdownDocumentChunker(DocumentChunker):
    """Chunker that parses markdown into semantic elements.

    Uses chonkie's MarkdownChef to extract tables, code blocks, and
    text chunks, then merges them sorted by position.
    Best suited for markdown documents with mixed content.
    """

    name = "markdown"

    def _chunk(self, text: str, config: dict[str, Any] | None) -> list[ChunkData]:
        parsed = MarkdownChunkerConfig(**(config or {}))
        doc = MarkdownChef().parse(text)
        tokenizer = AutoTokenizer("character")

        # Collect all elements: chunks, tables, code blocks
        elements: list[tuple[int, str]] = []
        for chunk in doc.chunks:
            elements.append((chunk.start_index, chunk.text))
        for table in doc.tables:
            elements.append((table.start_index, table.content))
        for code in doc.code:
            elements.append((code.start_index, code.content))

        # Sort by start_index
        elements.sort(key=lambda x: x[0])

        chunks = [
            Chunk(
                text=el_text,
                token_count=tokenizer.count_tokens(el_text),
                start_index=start_idx,
                end_index=start_idx + len(el_text),
            )
            for start_idx, el_text in elements
        ]
        return apply_chonkie(chunks, parsed.refineries)

    async def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split markdown text into semantic chunks.

        Args:
            text: The markdown text to chunk.
            config: Optional chunker configuration.

        Returns:
            List of ChunkData objects sorted by start_index.
        """
        return await asyncio.to_thread(self._chunk, text, config)
