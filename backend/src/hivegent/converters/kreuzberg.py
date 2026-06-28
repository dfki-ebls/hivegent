"""Kreuzberg-based document converter with native async support."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import PIL.Image
from kreuzberg import ExtractionConfig, ImageExtractionConfig, OcrConfig, extract_file
from pydantic import BaseModel, Field

from ..config import settings
from .base import ConversionResult, DocumentConverter, ExtractedImage, pil_to_png_bytes

__all__ = ["KreuzbergConverter", "KreuzbergConverterConfig"]


class KreuzbergConverterConfig(BaseModel):
    """Configuration for the Kreuzberg conversion pipeline."""

    force_ocr: bool = Field(
        default=False,
        description="Force OCR even when embedded text is available.",
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
    label = "Kreuzberg"
    description = "Text extraction from 75+ formats with OCR support"
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
    config: KreuzbergConverterConfig = field(default_factory=KreuzbergConverterConfig)

    async def _convert(self, path: Path, /) -> ConversionResult:
        extraction_config = ExtractionConfig(
            force_ocr=self.config.force_ocr,
            output_format="markdown",
            enable_quality_processing=self.config.enable_quality_processing,
            include_document_structure=self.config.include_document_structure,
            # Kreuzberg links its own libtesseract and resolves the language
            # packs via TESSDATA_PREFIX, same as the docling pipeline.
            ocr=OcrConfig(
                backend="tesseract",
                language="+".join(settings.conversion.ocr.languages),
            ),
            images=ImageExtractionConfig(
                inject_placeholders=True,  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]  # ty: ignore[unknown-argument]
            ),
        )
        result = await extract_file(path, config=extraction_config)
        markdown = str(result.content)

        # Kreuzberg injects ``![](image)`` as a uniform placeholder for every
        # image.  Replace each occurrence sequentially (same approach as the
        # Docling converter with ``<!-- image -->``).
        image_data: dict[str, ExtractedImage] = {}
        if result.images:
            for img in sorted(result.images, key=lambda i: i.get("image_index", 0)):
                img_name = f"image_{len(image_data):06}.png"
                raw: bytes = img["data"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
                fmt = img.get("format", "png")
                if fmt.lower() != "png":
                    pil_img = PIL.Image.open(BytesIO(raw))
                    raw = pil_to_png_bytes(pil_img)
                page_no = img.get("page_number")
                image_data[img_name] = ExtractedImage(
                    data=raw,
                    page_no=int(page_no) if page_no is not None else None,
                )
                markdown = markdown.replace("![](image)", f"![]({img_name})", 1)

        return ConversionResult(markdown=markdown, images=image_data)
