"""PDF page selection and rasterisation for vision models.

Two representations back the ``images`` and ``native`` binary-content
modes: :func:`render_pdf_pages` rasterises pages to still PNGs (for vision
servers that only accept ``image_url`` parts), and :func:`extract_pdf_pages`
carves a subset into a new PDF (for providers that ingest ``file`` parts
directly).

Both are thin async façades: the pdfium work runs in a throwaway spawned
worker (:mod:`hivegent.workers.pdf` via :func:`run_isolated`) because pdfium
segfaults on some malformed PDFs and fonts, and the input here is untrusted.
A worker crash surfaces as the same :class:`ValueError` an unreadable PDF
raises (:func:`run_isolated_untrusted`), instead of taking the whole server
down with it.
"""

from ..workers import pdf as worker
from ..workers.isolation import run_isolated_untrusted

# Re-exported so callers keep a single import surface for PDF paging.
parse_pages = worker.parse_pages

__all__ = [
    "DEFAULT_MAX_PAGES",
    "extract_pdf_pages",
    "parse_pages",
    "render_pdf_pages",
]

DEFAULT_MAX_PAGES = 16
"""Default upper bound of pages rasterised from one PDF."""


async def render_pdf_pages(
    pdf_bytes: bytes, spec: str | None, max_dimension: int, max_pages: int
) -> tuple[tuple[bytes, ...], tuple[int, ...]]:
    """Rasterise the requested PDF pages to downscaled PNG images.

    See :func:`hivegent.workers.pdf.render_pages`.  Raises :class:`ValueError`
    for an invalid page spec, a page count above *max_pages*, an unreadable
    PDF, or a native crash in the render worker.
    """
    return await run_isolated_untrusted(
        "PDF", worker.render_pages, pdf_bytes, spec, max_dimension, max_pages
    )


async def extract_pdf_pages(
    pdf_bytes: bytes, spec: str
) -> tuple[bytes, tuple[int, ...]]:
    """Return a new PDF containing only the *spec*-selected pages.

    See :func:`hivegent.workers.pdf.extract_pages`.  Raises
    :class:`ValueError` for an invalid page spec, an unreadable PDF, or a
    native crash in the worker.
    """
    return await run_isolated_untrusted("PDF", worker.extract_pages, pdf_bytes, spec)
