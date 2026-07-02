"""LibreOffice text-recovery fallback for Office documents docling cannot parse.

Docling's native Office backends load the package strictly (``python-docx``
eagerly resolves every image part), so a document that opens in Word with only
its images missing still aborts conversion.  LibreOffice opens such files
leniently: this recovers their text by exporting to HTML and rendering that to
markdown with pandoc — no intermediate PDF and no docling round-trip.  Images
are dropped (they are exactly what defeated the primary converter); this path
recovers text only.  It is a fallback, not a registered pipeline.
"""

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

# Drop every image on pandoc's document AST rather than string-matching the
# rendered markdown: a text regex is fragile against URLs with parentheses,
# reference-style links, and images nested inside links, while an AST filter
# removes the node cleanly and leaves the surrounding structure intact.
_DROP_IMAGES_FILTER = "function Image () return {} end\n"


async def recover_office_markdown(source: Path) -> str | None:
    """Recover *source*'s text as markdown via LibreOffice + pandoc.

    Returns the markdown, or ``None`` when LibreOffice is unavailable, the
    export fails, or nothing textual could be recovered.  Images are stripped by
    a pandoc filter (they are exactly what defeated the primary converter).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        html = await libreoffice_convert(source, tmp_dir, to="html")
        if html is None:
            return None

        drop_images = tmp_dir / "drop-images.lua"
        drop_images.write_text(_DROP_IMAGES_FILTER, encoding="utf-8")
        markdown = await pandoc_convert(
            html,
            to="gfm",
            from_format="html",
            extra_args=("--lua-filter", str(drop_images)),
        )

    return markdown.strip() or None
