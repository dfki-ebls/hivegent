"""Kreuzberg-based document converter with native async support."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kreuzberg import ExtractionConfig, extract_file

from .base import DocumentConverter
from .config import KreuzbergConverterConfig

__all__ = ["KreuzbergConverter"]


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
