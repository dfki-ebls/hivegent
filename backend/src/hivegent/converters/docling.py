"""Docling-based document converter."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from docling.datamodel.base_models import FormatToExtensions
from docling.document_converter import (
    DocumentConverter as DoclingDocumentConverter,
)

from .base import DocumentConverter

__all__ = ["DoclingConverter"]


# Derived from docling.datamodel.base_models.FormatToExtensions.
# https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py
@dataclass(slots=True, frozen=True)
class DoclingConverter(DocumentConverter):
    """Document converter using the Docling library.

    Docling provides high-quality document conversion with excellent support
    for Office documents (DOCX, XLSX, PPTX), PDFs, and images.
    """

    name = "docling"
    extensions = frozenset(
        f".{ext}" for exts in FormatToExtensions.values() for ext in exts
    )

    def _convert_sync(self, path: Path) -> str:
        """Run the synchronous Docling conversion."""
        result = DoclingDocumentConverter().convert(str(path))
        return str(result.document.export_to_markdown())

    async def __call__(self, path: Path, /) -> str:
        """Convert a document to markdown using Docling.

        Args:
            path: Path to the document to convert.

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path)
