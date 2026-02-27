"""Docling-based document converter."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import FormatToExtensions, InputFormat
from docling.datamodel.pipeline_options import (
    ConvertPipelineOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import DocumentConverter as DoclingDocumentConverter
from pydantic import BaseModel, Field

from .base import DocumentConverter

__all__ = ["DoclingConverter", "DoclingConverterConfig"]


class DoclingConverterConfig(BaseModel):
    """Configuration for the Docling conversion pipeline.

    Uses docling's own Pydantic option models.
    ``pdf_options`` applies to PDF and image formats;
    ``convert_options`` applies to Office and text formats.
    """

    pdf_options: ThreadedPdfPipelineOptions = Field(
        default_factory=ThreadedPdfPipelineOptions,
        description="Options for PDF and image formats (OCR, table structure, layout, etc.)",
    )
    convert_options: ConvertPipelineOptions = Field(
        default_factory=ConvertPipelineOptions,
        description="Options for Office and text formats (DOCX, PPTX, HTML, etc.)",
    )


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

        # Start from default format options (which include the correct backend
        # and pipeline_cls) and only override pipeline_options.
        converter = DoclingDocumentConverter()
        for fmt in converter.format_to_options:
            default = converter.format_to_options[fmt]
            opts = parsed.pdf_options if fmt in _PDF_FORMATS else parsed.convert_options
            converter.format_to_options[fmt] = default.model_copy(
                update={"pipeline_options": opts}
            )

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
