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
raises, instead of taking the whole server down with it.
"""

from collections.abc import Callable

from ..workers import pdf as worker
from ..workers.isolation import WorkerCrashError, run_isolated

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


async def _isolate[T](func: Callable[..., T], *args: object) -> T:
    """Run a pdfium worker call, mapping a native crash to :class:`ValueError`.

    A worker crash on a corrupt document surfaces as the same
    :class:`ValueError` an unreadable PDF raises, so callers handle one error.
    """
    try:
        return await run_isolated(func, *args)

    except WorkerCrashError as exc:
        raise ValueError("PDF rendering crashed on a corrupt document") from exc


async def render_pdf_pages(
    pdf_bytes: bytes, spec: str | None, max_dimension: int, max_pages: int
) -> tuple[tuple[bytes, ...], tuple[int, ...]]:
    """Rasterise the requested PDF pages to downscaled PNG images.

    See :func:`hivegent.workers.pdf.render_pages`.  Raises :class:`ValueError`
    for an invalid page spec, a page count above *max_pages*, an unreadable
    PDF, or a native crash in the render worker.
    """
    return await _isolate(
        worker.render_pages, pdf_bytes, spec, max_dimension, max_pages
    )


async def extract_pdf_pages(
    pdf_bytes: bytes, spec: str
) -> tuple[bytes, tuple[int, ...]]:
    """Return a new PDF containing only the *spec*-selected pages.

    See :func:`hivegent.workers.pdf.extract_pages`.  Raises
    :class:`ValueError` for an invalid page spec, an unreadable PDF, or a
    native crash in the worker.
    """
    return await _isolate(worker.extract_pages, pdf_bytes, spec)
