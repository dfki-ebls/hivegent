"""Kreuzberg-based document converter with native async support."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kreuzberg import ExtractionConfig, extract_file
from pydantic import BaseModel, Field

from .base import DocumentConverter

__all__ = ["KreuzbergConverter", "KreuzbergConverterConfig"]


class KreuzbergConverterConfig(BaseModel):
    """Configuration for the Kreuzberg conversion pipeline."""

    force_ocr: bool = Field(
        default=False,
        description="Force OCR even when embedded text is available.",
    )
    output_format: str = Field(
        default="plain",
        description="Output format ('plain' or 'markdown').",
    )
    enable_quality_processing: bool = Field(
        default=True,
        description="Enable quality post-processing of extracted text.",
    )
    include_document_structure: bool = Field(
        default=False,
        description="Include structural elements (headings, lists) in output.",
    )


# Kreuzberg exposes get_extensions_for_mime() per MIME type but has no
# API to enumerate all supported types at once.
# https://docs.kreuzberg.dev/features/supported-formats/
@dataclass(slots=True, frozen=True)
class KreuzbergConverter(DocumentConverter):
    """Document converter using the Kreuzberg text extraction library.

    Kreuzberg extracts text from 75+ file formats including Office documents,
    PDFs, images (with OCR), and many more. It provides a native async API,
    so no thread wrapping is needed.
    """

    name = "kreuzberg"
    extensions = frozenset(
        {
            ".pdf",
            ".docx",
            ".xlsx",
            ".pptx",
            ".doc",
            ".xls",
            ".ppt",
            ".odt",
            ".ods",
            ".html",
            ".htm",
            ".xml",
            ".json",
            ".csv",
            ".epub",
            ".rtf",
            ".txt",
            ".md",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".tiff",
            ".tif",
            ".bmp",
            ".svg",
            ".ico",
            ".msg",
            ".eml",
            ".zip",
            ".tar",
            ".gz",
            ".7z",
        }
    )

    async def __call__(
        self,
        path: Path,
        /,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Convert a document to plain text using Kreuzberg.

        Args:
            path: Path to the document to convert.
            config: Optional Kreuzberg pipeline configuration.

        Returns:
            The extracted text content.
        """
        parsed = KreuzbergConverterConfig(**(config or {}))
        extraction_config = ExtractionConfig(
            force_ocr=parsed.force_ocr,
            output_format=parsed.output_format,
            enable_quality_processing=parsed.enable_quality_processing,
            include_document_structure=parsed.include_document_structure,
        )
        result = await extract_file(path, config=extraction_config)
        return str(result.content)
