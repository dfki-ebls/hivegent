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
    description: ClassVar[str] = "Plain text files as-is"
    extensions: ClassVar[frozenset[str]] = frozenset({".txt"})

    async def _convert(self, path: Path, /) -> ConversionResult:
        doc = await asyncio.to_thread(_build_chef().process, path)
        return ConversionResult(markdown=doc.content)
