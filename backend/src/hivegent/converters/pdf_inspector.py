"""pdf-inspector-based PDF converter."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pdf_inspector
from pydantic import BaseModel, ConfigDict

from .base import ConversionResult, DocumentConverter

__all__ = ["PdfInspectorConverter", "PdfInspectorConverterConfig"]


class PdfInspectorConverterConfig(BaseModel):
    """Configuration for the pdf-inspector conversion pipeline.

    ``page_markers`` interleaves ``<!-- Page N -->`` comments so a chunk can be
    traced back to its page.  It switches extraction to per-page mode, which
    costs document-wide heading calibration: headings are then sized against
    the fonts of their own page rather than those of the whole document.
    """

    model_config = ConfigDict(extra="forbid")

    page_markers: bool = False


@dataclass(slots=True, frozen=True)
class PdfInspectorConverter(DocumentConverter):
    """PDF converter using Firecrawl's pdf-inspector Rust library.

    Reconstructs the text layer with position awareness — multi-column reading
    order, heading levels from font-size ratios, and tables recovered both from
    the PDF's own drawing operators and from text alignment — in tens of
    milliseconds and without any model.

    It runs no OCR and extracts no images, so a scanned or image-only PDF
    raises instead of returning an empty document.  Those belong on docling,
    whose Tesseract stage
    :func:`~hivegent.converters.pdf_classify.pdf_has_text_layer` gates with the
    same classifier.
    """

    name: ClassVar[str] = "pdf-inspector"
    config: PdfInspectorConverterConfig = field(
        default_factory=PdfInspectorConverterConfig
    )

    def _extract(self, source: str) -> str:
        """Extract markdown, per page when markers are on and whole-document otherwise."""
        if not self.config.page_markers:
            return pdf_inspector.process_pdf(source).markdown or ""

        extraction = pdf_inspector.extract_pages_markdown(source)
        return "\n\n".join(
            f"<!-- Page {page.page + 1} -->\n\n{page.markdown}".rstrip()
            for page in extraction.pages
        )

    def _convert_sync(self, path: Path, /) -> ConversionResult:
        source = str(path)
        markdown = self._extract(source)
        if not markdown.strip():
            # Only this failure path pays for a second pass over the document.
            pdf_type = pdf_inspector.detect_pdf(source).pdf_type
            msg = (
                f"{path.name} carries no extractable text layer "
                f"(classified as {pdf_type}); it needs an OCR pipeline"
            )
            raise ValueError(msg)

        return ConversionResult(markdown=markdown)
