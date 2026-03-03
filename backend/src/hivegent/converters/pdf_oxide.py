"""pdf_oxide-based PDF converter."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from pdf_oxide import PdfDocument
from pydantic import BaseModel

from .base import DocumentConverter

__all__ = ["PdfOxideConverter", "PdfOxideConverterConfig"]


class PdfOxideConverterConfig(BaseModel):
    """Configuration for the pdf_oxide conversion pipeline."""


@dataclass(slots=True, frozen=True)
class PdfOxideConverter(DocumentConverter):
    """PDF converter using the pdf_oxide library.

    pdf_oxide is a high-performance Rust-based PDF processing library with
    Python bindings that supports text extraction and markdown conversion.
    """

    name = "pdf-oxide"
    extensions = frozenset({".pdf"})
    config: PdfOxideConverterConfig = field(default_factory=PdfOxideConverterConfig)

    def _convert_sync(self, path: Path) -> str:
        """Run the synchronous pdf_oxide conversion."""
        doc = PdfDocument(str(path))
        pages = [doc.to_markdown(page) for page in range(doc.page_count())]
        return "\n\n".join(pages)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> str:
        """Convert a PDF document to markdown using pdf_oxide.

        Args:
            path: Path to the PDF document to convert.

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path)
