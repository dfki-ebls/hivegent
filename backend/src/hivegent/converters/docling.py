"""Docling-based document converter."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import FormatToExtensions, InputFormat
from docling.datamodel.pipeline_options import PipelineOptions
from docling.document_converter import (
    DocumentConverter as DoclingDocumentConverter,
    FormatOption,
)

from .base import DocumentConverter
from .config import DoclingConverterConfig

__all__ = ["DoclingConverter"]

# Formats that use the threaded PDF pipeline options.
_PDF_FORMATS = frozenset({InputFormat.PDF, InputFormat.IMAGE, InputFormat.METS_GBS})


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

    def _convert_sync(self, path: Path, config: dict[str, Any] | None) -> str:
        """Run the synchronous Docling conversion."""
        parsed = DoclingConverterConfig(**(config or {}))

        # Build format options: PDF/image formats use pdf_options,
        # everything else uses convert_options.
        format_options: dict[InputFormat, FormatOption] = {}
        for fmt in InputFormat:
            if fmt in _PDF_FORMATS:
                opts: PipelineOptions = parsed.pdf_options
            else:
                opts = parsed.convert_options
            format_options[fmt] = FormatOption(pipeline_options=opts)

        converter = DoclingDocumentConverter(format_options=format_options)
        result = converter.convert(str(path))
        return str(result.document.export_to_markdown())

    async def __call__(
        self,
        path: Path,
        /,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Convert a document to markdown using Docling.

        Args:
            path: Path to the document to convert.
            config: Optional Docling pipeline configuration.

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path, config)
