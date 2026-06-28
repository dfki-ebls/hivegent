"""pdf_oxide-based PDF converter."""

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pdf_oxide import PdfDocument
from pydantic import BaseModel

from .base import ConversionResult, DocumentConverter, collect_dir_images

__all__ = ["PdfOxideConverter", "PdfOxideConverterConfig"]


class PdfOxideConverterConfig(BaseModel):
    """Configuration for the pdf_oxide conversion pipeline.

    Mirrors pdf_oxide's ``to_markdown_all`` knobs.  ``preserve_layout``
    keeps the visual column/whitespace layout (off by default: a single
    reading-order flow chunks better for retrieval than reconstructed
    columns); ``detect_headings`` promotes detected headings to markdown
    ``#`` levels so the chunker can split on them.
    """

    preserve_layout: bool = False
    detect_headings: bool = True


@dataclass(slots=True, frozen=True)
class PdfOxideConverter(DocumentConverter):
    """PDF converter using the pdf_oxide library.

    pdf_oxide is a high-performance Rust-based PDF processing library with
    Python bindings that supports text extraction and markdown conversion.
    """

    name = "pdf-oxide"
    label = "pdf_oxide"
    description = "High-performance Rust-based PDF to markdown converter"
    extensions = frozenset({".pdf"})
    config: PdfOxideConverterConfig = field(default_factory=PdfOxideConverterConfig)

    def _convert_sync(self, path: Path) -> ConversionResult:
        """Run the synchronous pdf_oxide conversion."""
        doc = PdfDocument(str(path))
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # One whole-document call (cheaper than a per-page Python loop and
            # lets pdf_oxide resolve reading order across pages). ``include_images``
            # must be set explicitly: it defaults to False, so the previous
            # per-page calls extracted no images at all.
            markdown = doc.to_markdown_all(
                preserve_layout=self.config.preserve_layout,
                detect_headings=self.config.detect_headings,
                include_images=True,
                embed_images=False,
                image_output_dir=str(temp_path),
            )
            # Collect extracted images before the temp dir is cleaned up.
            image_data = collect_dir_images(temp_path, temp_path)
            # pdf_oxide references images by absolute path; rewrite to the bare
            # filename so refs match the (throwaway-dir-free) image keys.
            markdown = markdown.replace(f"{temp_path}/", "")
            return ConversionResult(markdown=markdown, images=image_data)

    async def _convert(self, path: Path, /) -> ConversionResult:
        return await asyncio.to_thread(self._convert_sync, path)
