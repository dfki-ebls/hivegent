"""Poppler text-recovery fallback for PDFs whose fonts lack a ToUnicode CMap.

Some legacy PDFs (typically old Microsoft producers with ``MSTT`` subset fonts)
embed subsetted fonts under a custom encoding and no ToUnicode table.  Docling's
glyph-id text backend then dumps the raw glyph names (``/G56/G6F/G6C``...) into
the markdown instead of characters.  poppler carries a glyph-name to Unicode
heuristic (as macOS Preview does), so ``pdftotext`` recovers the real text.
This is a fallback, not a registered pipeline.
"""

import re
from pathlib import Path

from ..subprocesses import SubprocessError, pdftotext_convert

__all__ = ["is_pdf_text_garbled", "recover_pdf_markdown"]


# A run of three or more consecutive ``/name`` glyph tokens with no separator is
# the signature of glyph-name dumping; legitimate text never packs slash-tokens
# this way.  The fraction of characters inside such runs cleanly separates
# garbled output (>0.9 in practice) from real documents (<0.02).
_GLYPH_RUN_RE = re.compile(r"(?:/[A-Za-z][A-Za-z0-9]{0,6}){3,}")
_GARBLED_RATIO_THRESHOLD = 0.25


def is_pdf_text_garbled(text: str) -> bool:
    """Return whether *text* is dominated by glyph-name gibberish.

    >>> is_pdf_text_garbled("## /G56/G6F/G6C/G75/G6D/G65/G6E")
    True
    >>> is_pdf_text_garbled("# Volumenstromangaben in der Drucklufttechnik")
    False
    """
    non_space = sum(1 for c in text if not c.isspace())
    if non_space == 0:
        return False

    run_chars = sum(
        sum(1 for c in m.group() if not c.isspace())
        for m in _GLYPH_RUN_RE.finditer(text)
    )
    return run_chars / non_space >= _GARBLED_RATIO_THRESHOLD


async def recover_pdf_markdown(source: Path) -> str | None:
    """Recover *source*'s text via poppler ``pdftotext``.

    Returns the recovered text, or ``None`` when poppler is unavailable, the
    extraction fails, or the result is itself empty or still garbled — so the
    caller keeps the primary converter's output rather than replacing it with
    something no better.
    """
    try:
        text = (await pdftotext_convert(source)).strip()
    except (SubprocessError, OSError):
        return None

    if not text or is_pdf_text_garbled(text):
        return None

    return text
