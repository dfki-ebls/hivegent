"""Plain text document converter using chonkie's TextChef."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from chonkie import TextChef

from .base import DocumentConverter

__all__ = ["ChonkieTextConverter"]


@dataclass(slots=True, frozen=True)
class ChonkieTextConverter(DocumentConverter):
    """Converter that reads plain text files.

    Uses chonkie's TextChef to process plain text files as-is.
    """

    name: ClassVar[str] = "text-chef"
    extensions: ClassVar[frozenset[str]] = frozenset({".txt"})

    async def __call__(
        self,
        path: Path,
        /,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Convert a plain text file to markdown.

        Args:
            path: Path to the text file.
            config: Unused.

        Returns:
            The text content.
        """
        doc = await asyncio.to_thread(TextChef().process, path)
        return doc.content
