"""Plain text document converter using chonkie's TextChef."""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from chonkie import TextChef

from .base import ConversionResult, DocumentConverter

__all__ = ["ChonkieTextConverter"]


@lru_cache(maxsize=4)
def _build_chef() -> TextChef:
    return TextChef()


@dataclass(slots=True, frozen=True)
class ChonkieTextConverter(DocumentConverter):
    """Converter that reads plain text files.

    Uses chonkie's TextChef to process plain text files as-is.
    """

    name: ClassVar[str] = "text-chef"
    label: ClassVar[str] = "Text Chef"
    description: ClassVar[str] = (
        "Plain text, config, and data-serialization files as-is"
    )
    # Raw-text formats with no richer converter, read verbatim. Notably ``.json``
    # must land here and not on docling, which only accepts its own
    # ``DoclingDocument`` JSON schema and rejects ordinary JSON as invalid.
    # Structured text that converts to better markdown (csv/tsv tables, html,
    # xml, rst, org, latex, ...) is deliberately left to docling/pandoc.
    extensions: ClassVar[frozenset[str]] = frozenset(
        {
            ".txt",
            ".text",
            ".log",
            ".json",
            ".jsonl",
            ".ndjson",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".env",
        }
    )

    async def _convert(self, path: Path, /) -> ConversionResult:
        doc = await asyncio.to_thread(_build_chef().process, path)
        return ConversionResult(markdown=doc.content)
