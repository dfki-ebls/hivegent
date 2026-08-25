"""Markdown-aware document chunker using chonkie."""

from dataclasses import dataclass, field
from functools import lru_cache

from chonkie import MarkdownChef
from chonkie.tokenizer import AutoTokenizer
from chonkie.types.base import Chunk

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["MarkdownChunkerConfig", "MarkdownDocumentChunker"]


class MarkdownChunkerConfig(BaseChonkieConfig):
    """Configuration for the Markdown chunking pipeline."""


@lru_cache(maxsize=4)
def _build_chef() -> MarkdownChef:
    return MarkdownChef()


@lru_cache(maxsize=4)
def _build_tokenizer() -> AutoTokenizer:
    return AutoTokenizer("character")


@dataclass(slots=True, frozen=True)
class MarkdownDocumentChunker(DocumentChunker):
    """Chunker that parses markdown into semantic elements.

    Uses chonkie's MarkdownChef to extract tables, code blocks, and
    text chunks, then merges them sorted by position.
    Best suited for markdown documents with mixed content.
    """

    name = "markdown"
    config: MarkdownChunkerConfig = field(default_factory=MarkdownChunkerConfig)

    def _split_sync(self, text: str) -> list[ChunkData]:
        doc = _build_chef().parse(text)
        tokenizer = _build_tokenizer()

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
        return apply_chonkie(chunks, self.config.refineries)
