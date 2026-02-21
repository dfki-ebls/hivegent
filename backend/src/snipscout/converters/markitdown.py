"""MarkItDown-based document converter with lazy loading."""

import asyncio
from pathlib import Path
from typing import Any

from .base import DocumentConverter

__all__ = ["MarkItDownConverter"]


class MarkItDownConverter(DocumentConverter):
    """Document converter using Microsoft's MarkItDown library.

    MarkItDown converts Office documents, PDFs, images, HTML, and other
    formats to markdown. This converter uses lazy imports to avoid loading
    the dependencies until needed.
    """

    @property
    def name(self) -> str:
        """The unique name of this converter."""
        return "markitdown"

    def __init__(self) -> None:
        """Initialize the converter with lazy loading."""
        self._converter: Any = None

    def _convert_sync(self, file_path: Path) -> str:
        """Run the synchronous MarkItDown conversion.

        Raises:
            ImportError: If markitdown is not installed.
        """
        if self._converter is None:
            try:
                from markitdown import MarkItDown

                self._converter = MarkItDown()
            except ImportError as e:
                raise ImportError(
                    "markitdown is not installed. "
                    "Install with: pip install 'markitdown[all]'"
                ) from e

        result = self._converter.convert(str(file_path))
        return str(result.text_content)

    async def convert(self, file_path: Path) -> str:
        """Convert a document to markdown using MarkItDown.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The document content converted to markdown.

        Raises:
            ImportError: If markitdown is not installed.
        """
        return await asyncio.to_thread(self._convert_sync, file_path)
