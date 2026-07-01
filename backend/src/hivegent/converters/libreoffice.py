"""LibreOffice text-recovery fallback for Office documents docling cannot parse.

Docling's native Office backends load the package strictly (``python-docx``
eagerly resolves every image part), so a document that opens in Word with only
its images missing still aborts conversion.  LibreOffice opens such files
leniently: this recovers their text by exporting to HTML and rendering that to
markdown with pandoc — no intermediate PDF and no docling round-trip.  Images
are dropped (they are exactly what defeated the primary converter); this path
recovers text only.  It is a fallback, not a registered pipeline.
"""

import re
import tempfile
from pathlib import Path

from ..subprocesses import libreoffice_convert, pandoc_convert

__all__ = ["OFFICE_FALLBACK_SUFFIXES", "recover_office_markdown"]

# Office formats LibreOffice opens and can export to HTML.
OFFICE_FALLBACK_SUFFIXES = frozenset(
    {
        ".doc", ".docx", ".docm", ".dot", ".dotx",
        ".odt", ".rtf",
        ".ppt", ".pptx", ".pptm", ".odp",
        ".xls", ".xlsx", ".xlsm", ".ods",
    }
)

_IMAGE_REF = re.compile(r"!\[[^\]]*\]\([^)]*\)")


async def recover_office_markdown(source: Path) -> str | None:
    """Recover *source*'s text as markdown via LibreOffice + pandoc.

    Returns the markdown, or ``None`` when LibreOffice is unavailable, the
    export fails, or nothing textual could be recovered.
    """
    with tempfile.TemporaryDirectory() as tmp:
        html = await libreoffice_convert(source, Path(tmp), to="html")
        if html is None:
            return None

        markdown = await pandoc_convert(html, to="gfm", from_format="html")

    return _IMAGE_REF.sub("", markdown).strip() or None
