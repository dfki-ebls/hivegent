"""PDF classification probes shared by the converters."""

from pathlib import Path

import pdf_inspector

__all__ = ["pdf_has_text_layer"]


def pdf_has_text_layer(path: Path) -> bool:
    """Return whether *path* carries an extractable text layer on most pages.

    Classifies the whole document by sampling its content streams — no
    rendering, no models, a couple of milliseconds — and answers whether fewer
    than half of its pages need OCR.  Returns ``False`` when classification
    fails, so an unreadable or unusual PDF still gets an OCR pass instead of
    silently losing its text.
    """
    try:
        classification = pdf_inspector.classify_pdf(str(path))
    except Exception:  # noqa: BLE001
        # Deliberately broad: the Rust bindings document no error taxonomy, and
        # this probe only advises whether to OCR.  Answering "no text layer"
        # costs a needless OCR pass; letting an unexpected error escape would
        # fail the whole conversion instead.
        return False

    pages = classification.page_count
    return pages > 0 and 2 * len(classification.pages_needing_ocr) < pages
