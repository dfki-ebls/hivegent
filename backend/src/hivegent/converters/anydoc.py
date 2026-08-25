"""anydoc-based converter for office and publishing formats."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import anydoc

from .base import ConversionResult, DocumentConverter

__all__ = ["AnydocConverter"]


@dataclass(slots=True, frozen=True)
class AnydocConverter(DocumentConverter):
    """Document converter using Firecrawl's anydoc Rust library.

    Every format parses into one shared document model and renders through a
    single markdown serializer, so headings, tables, lists, and footnotes come
    out the same whichever format went in.  There are no models and no
    subprocess, which puts a typical document well under a millisecond and
    makes this the cheap structural alternative to docling and pandoc.

    Embedded images stay out of the result.  anydoc's serializer renders them
    as their alt text alone, so the markdown holds no reference to pair them
    with, and reuniting the two would mean rendering from anydoc's document
    model rather than calling ``to_markdown`` — the whole serializer.
    Documents whose figures carry meaning belong on docling.  Images that name
    an external URL are unaffected and pass through as markdown images.

    The registry deliberately excludes ``.pdf`` even though anydoc reads it.
    It delegates PDFs to a vendored copy of pdf-inspector, which
    :class:`~hivegent.converters.pdf_inspector.PdfInspectorConverter` drives
    directly at its own, newer version.
    """

    name: ClassVar[str] = "anydoc"

    def _convert_sync(self, path: Path, /) -> ConversionResult:
        # ``to_markdown`` detects the format from the content and falls back to
        # the extension for the signature-less ones (CSV).
        return ConversionResult(markdown=anydoc.to_markdown(path))
