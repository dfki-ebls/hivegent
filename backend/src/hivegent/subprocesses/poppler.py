"""Typed async wrapper around poppler's ``pdftotext``."""

from pathlib import Path

from .base import run

__all__ = ["pdftotext_convert"]


async def pdftotext_convert(source: Path, *, layout: bool = False) -> str:
    """Extract a PDF's text with poppler's ``pdftotext``.

    poppler reconstructs Unicode from a font's glyph-name convention
    (``gXX``/``GXX``/``uniXXXX``) when the PDF carries no ToUnicode CMap, so it
    recovers text that glyph-id backends (docling, pdfium) dump as raw ``/GXX``
    glyph names.

    Args:
        source: Path to the input PDF.
        layout: Preserve the physical column/whitespace layout.  Off by
            default: a single reading-order flow chunks better for retrieval
            than reconstructed columns.

    Returns:
        The extracted text as UTF-8, with page-break form feeds normalised to
        blank lines.
    """
    args: list[str | Path] = ["pdftotext", "-enc", "UTF-8", "-q"]
    if layout:
        args.append("-layout")
    args.extend([source, "-"])

    result = await run(args)
    return result.stdout_text.replace("\f", "\n\n")
