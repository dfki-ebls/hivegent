"""pypdfium2 render/extract bodies, isolated in a spawned worker.

Run in a throwaway process via :func:`hivegent.workers.isolation.run_isolated`
(driven by :mod:`hivegent.converters.pdf_raster`), so a pdfium crash on a
malformed document cannot take the server down.

Kept deliberately dependency-light: a spawned worker imports this module to
unpickle its target, and importing anything under ``hivegent.converters``
would drag the whole converter registry (logfire, requests, ...) into every
worker — roughly 200ms of avoidable startup.  Top-level imports stay
stdlib + the shared leaf image helper; ``pypdfium2`` loads lazily inside the
functions.
"""

import io
import re

from ..imaging import pil_to_still_png

__all__ = ["extract_pages", "parse_pages", "render_pages"]

# Bounded digit count keeps a malicious `pages='9'*1e9` from forcing CPython
# to allocate a multi-megabyte int before the range check rejects it.
_PAGE_TOKEN_RE = re.compile(r"^(\d{1,9})(?:-(\d{1,9}))?$")


def parse_pages(spec: str, total: int) -> tuple[int, ...]:
    """Parse a page spec like ``1,3,5-7`` into a tuple of 1-based pages.

    >>> parse_pages("1,3,5-7", 10)
    (1, 3, 5, 6, 7)
    """
    seen: dict[int, None] = {}
    for raw in spec.split(","):
        match = _PAGE_TOKEN_RE.match(raw.strip())
        if not match:
            raise ValueError(f"Invalid page token: {raw.strip()!r}")

        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or end < start:
            raise ValueError(f"Invalid page range: {raw.strip()!r}")
        if end > total:
            raise ValueError(f"Page {end} exceeds document length {total}")

        for p in range(start, end + 1):
            seen.setdefault(p, None)

    return tuple(seen)


def render_pages(
    pdf_bytes: bytes, spec: str | None, max_dimension: int, max_pages: int
) -> tuple[tuple[bytes, ...], tuple[int, ...]]:
    """Rasterise the requested PDF pages to downscaled PNG images.

    *spec* selects 1-based pages (see :func:`parse_pages`); ``None`` renders
    the whole document.  Each page is rendered so its longer side lands near
    *max_dimension* pixels.  Raises :class:`ValueError` for an invalid page
    spec, a page count above *max_pages*, or an unreadable PDF.
    """
    import pypdfium2 as pdfium

    try:
        with pdfium.PdfDocument(pdf_bytes) as doc:
            total = len(doc)
            pages = (
                parse_pages(spec, total)
                if spec is not None
                else tuple(range(1, total + 1))
            )
            if len(pages) > max_pages:
                raise ValueError(
                    f"{len(pages)} pages exceeds the {max_pages}-page limit — "
                    "narrow with pages="
                )

            images: list[bytes] = []
            for p in pages:
                page = doc[p - 1]
                width, height = page.get_size()
                scale = max_dimension / max(width, height)
                images.append(
                    pil_to_still_png(page.render(scale=scale).to_pil(), max_dimension)
                )

            return tuple(images), pages

    except pdfium.PdfiumError as exc:
        raise ValueError(f"PDF could not be opened: {exc}") from exc


def extract_pages(pdf_bytes: bytes, spec: str) -> tuple[bytes, tuple[int, ...]]:
    """Return a new PDF containing only the *spec*-selected pages.

    Raises :class:`ValueError` for an invalid page spec or an unreadable PDF.
    """
    import pypdfium2 as pdfium

    try:
        with (
            pdfium.PdfDocument(pdf_bytes) as src,
            pdfium.PdfDocument.new() as dst,
        ):
            pages = parse_pages(spec, len(src))
            dst.import_pages(src, pages=[p - 1 for p in pages])
            buf = io.BytesIO()
            dst.save(buf)
            return buf.getvalue(), pages

    except pdfium.PdfiumError as exc:
        raise ValueError(f"PDF could not be opened: {exc}") from exc
