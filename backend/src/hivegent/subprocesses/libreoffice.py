"""Typed async wrapper around headless LibreOffice (``soffice``)."""

import logging
import shutil
from pathlib import Path

from .base import run

__all__ = ["libreoffice_command", "libreoffice_convert"]

logger = logging.getLogger(__name__)


def libreoffice_command() -> str | None:
    """Return the LibreOffice CLI on PATH, or ``None`` when unavailable.

    Prefers ``soffice`` over ``libreoffice`` so the shipped private-profile
    wrapper (which hands each run a throwaway user profile) is used ahead of
    any bare ``libreoffice`` that might sit earlier on the system PATH.
    """
    return shutil.which("soffice") or shutil.which("libreoffice")


async def libreoffice_convert(source: Path, out_dir: Path, *, to: str) -> Path | None:
    """Convert *source* to the *to* format in *out_dir* via headless LibreOffice.

    LibreOffice opens Office documents as leniently as Word, so it recovers
    files whose broken image package structure makes stricter parsers (e.g.
    ``python-docx``) reject them outright.  *to* is a LibreOffice
    ``--convert-to`` target (e.g. ``"html"``, ``"txt:Text"``).  Returns the
    produced file path, or ``None`` when LibreOffice is unavailable or the
    conversion fails (its stderr is logged for diagnosis).
    """
    cmd = libreoffice_command()
    if cmd is None:
        return None

    result = await run(
        [cmd, "--headless", "--convert-to", to, "--outdir", out_dir, source],
        check=False,
    )
    output = out_dir / f"{source.stem}.{to.split(':', 1)[0]}"
    if result.returncode != 0 or not output.is_file():
        logger.warning(
            "LibreOffice failed to convert %s to %s (exit %d): %s",
            source.name,
            to,
            result.returncode,
            result.stderr_text.strip(),
        )
        return None

    return output
