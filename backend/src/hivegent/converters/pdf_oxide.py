"""pdf_oxide-based PDF converter."""

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from pdf_oxide import OcrConfig, OcrEngine, PdfDocument
from pydantic import BaseModel

from ..config import settings
from .base import ConversionResult, DocumentConverter, collect_dir_images

__all__ = ["PdfOxideConverter", "PdfOxideConverterConfig"]


class _MarkdownOptions(TypedDict):
    """Shared keyword arguments for ``to_markdown`` and ``to_markdown_all``."""

    preserve_layout: bool
    detect_headings: bool
    include_images: bool
    embed_images: bool
    image_output_dir: str


@lru_cache(maxsize=1)
def _build_ocr_engine(
    det_model: str, rec_model: str, dict_path: str, num_threads: int
) -> OcrEngine:
    """Build a pdf_oxide OCR engine; cached because loading the ONNX models is costly.

    Single-slot: the model paths and thread count are fixed per deployment, so
    only one engine is ever built — the bound just stops a reconfigured path
    from leaving the old ONNX models resident.
    """
    return OcrEngine(
        det_model, rec_model, dict_path, OcrConfig(num_threads=num_threads)
    )


class PdfOxideConverterConfig(BaseModel):
    """Configuration for the pdf_oxide conversion pipeline.

    Mirrors pdf_oxide's ``to_markdown_all`` knobs.  ``preserve_layout``
    keeps the visual column/whitespace layout (off by default: a single
    reading-order flow chunks better for retrieval than reconstructed
    columns); ``detect_headings`` promotes detected headings to markdown
    ``#`` levels so the chunker can split on them.

    OCR is opt-in and stays off unless all three PaddleOCR ONNX paths are
    set (pdf_oxide bundles no models).  When configured, pages that
    ``classify_document`` reports as image-only are run through
    ``extract_text_ocr`` while text-layer pages keep their richer
    markdown; the OCR thread count comes from the shared
    ``compute.num_threads`` setting.
    """

    preserve_layout: bool = False
    detect_headings: bool = True
    ocr_det_model: str | None = None
    ocr_rec_model: str | None = None
    ocr_dict: str | None = None


@dataclass(slots=True, frozen=True)
class PdfOxideConverter(DocumentConverter):
    """PDF converter using the pdf_oxide library.

    pdf_oxide is a high-performance Rust-based PDF processing library with
    Python bindings that supports text extraction, markdown conversion, and
    optional PaddleOCR-based OCR for scanned pages.
    """

    name = "pdf-oxide"
    label = "pdf_oxide"
    description = "High-performance Rust-based PDF to markdown converter"
    extensions = frozenset({".pdf"})
    config: PdfOxideConverterConfig = field(default_factory=PdfOxideConverterConfig)

    def _ocr_engine(self) -> OcrEngine | None:
        """Return a cached OCR engine when all model paths are set, else None."""
        cfg = self.config
        if not (cfg.ocr_det_model and cfg.ocr_rec_model and cfg.ocr_dict):
            return None
        return _build_ocr_engine(
            cfg.ocr_det_model,
            cfg.ocr_rec_model,
            cfg.ocr_dict,
            settings.compute.num_threads,
        )

    def _convert_sync(self, path: Path) -> ConversionResult:
        """Run the synchronous pdf_oxide conversion, OCR-ing scanned pages if enabled."""
        doc = PdfDocument(str(path))
        engine = self._ocr_engine()
        # Only classify when OCR is available; ``pages_needing_ocr`` then drives
        # the per-page fallback (a cheap check, no rasterisation).
        needs_ocr: set[int] = set()
        if engine is not None:
            classification = json.loads(doc.classify_document())
            needs_ocr = set(classification.get("pages_needing_ocr", []))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options: _MarkdownOptions = {
                "preserve_layout": self.config.preserve_layout,
                "detect_headings": self.config.detect_headings,
                # ``include_images`` must be set explicitly: it defaults to False,
                # so without it pdf_oxide extracts no images at all.
                "include_images": True,
                "embed_images": False,
                "image_output_dir": str(temp_path),
            }
            if needs_ocr:
                # Mixed document: OCR the image-only pages and keep the richer
                # markdown for text-layer pages, preserving page order.
                markdown = "\n\n".join(
                    doc.extract_text_ocr(page, engine)
                    if page in needs_ocr
                    else doc.to_markdown(page, **options)
                    for page in range(doc.page_count())
                )
            else:
                # One whole-document call (cheaper than a per-page Python loop and
                # lets pdf_oxide resolve reading order across pages).
                markdown = doc.to_markdown_all(**options)

            # Collect extracted images before the temp dir is cleaned up.
            image_data = collect_dir_images(temp_path, temp_path)
            # pdf_oxide references images by absolute path; rewrite to the bare
            # filename so refs match the (throwaway-dir-free) image keys.
            markdown = markdown.replace(f"{temp_path}/", "")
            return ConversionResult(markdown=markdown, images=image_data)

    async def _convert(self, path: Path, /) -> ConversionResult:
        return await asyncio.to_thread(self._convert_sync, path)
