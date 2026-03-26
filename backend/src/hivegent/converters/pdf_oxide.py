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

    def _convert_sync(self, path: Path) -> ConversionResult:
        """Run the synchronous pdf_oxide conversion."""
        doc = PdfDocument(str(path))
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            pages = [
                doc.to_markdown(
                    page,
                    image_output_dir=str(temp_path),
                    embed_images=False,
                )
                for page in range(doc.page_count())
            ]
            # Collect extracted images before the temp dir is cleaned up.
            image_data = collect_dir_images(temp_path, temp_path)
            return ConversionResult(
                markdown="\n\n".join(pages), images=image_data
            )

    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a PDF document to markdown using pdf_oxide.

        Args:
            path: Path to the PDF document to convert.

        Returns:
            The conversion result with markdown content.
        """
        return await asyncio.to_thread(self._convert_sync, path)
