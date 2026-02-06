"""Docling-based document converter with lazy loading."""

import asyncio
from pathlib import Path
from typing import Any

from .base import DocumentConverter

__all__ = ["DoclingConverter"]

_SUPPORTED_EXTENSIONS = frozenset({
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
})


class DoclingConverter(DocumentConverter):
    """Document converter using the Docling library.

    Docling provides high-quality document conversion with excellent support
    for Office documents (DOCX, XLSX, PPTX), PDFs, and images. This converter
    uses lazy imports to avoid loading the heavy dependencies until needed.
    """

    @property
    def name(self) -> str:
        """The unique name of this converter."""
        return "docling"

    @property
    def supported_extensions(self) -> frozenset[str]:
        """File extensions supported by this converter."""
        return _SUPPORTED_EXTENSIONS

    def __init__(self) -> None:
        """Initialize the converter with lazy loading."""
        self._converter: Any = None

    def _convert_sync(self, file_path: Path) -> str:
        """Run the synchronous Docling conversion.

        Raises:
            ImportError: If docling is not installed.
        """
        if self._converter is None:
            try:
                from docling.document_converter import DocumentConverter

                self._converter = DocumentConverter()
            except ImportError as e:
                raise ImportError(
                    "docling is not installed. Install with: pip install docling"
                ) from e

        result = self._converter.convert(str(file_path))
        return str(result.document.export_to_markdown())

    async def convert(self, file_path: Path) -> str:
        """Convert a document to markdown using Docling.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The document content converted to markdown.

        Raises:
            ImportError: If docling is not installed.
        """
        return await asyncio.to_thread(self._convert_sync, file_path)
