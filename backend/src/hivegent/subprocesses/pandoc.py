"""Typed async wrapper around ``pandoc``."""

from collections.abc import Sequence
from pathlib import Path

from .base import run

__all__ = ["pandoc_convert"]


async def pandoc_convert(
    source: Path,
    *,
    to: str = "markdown",
    from_format: str | None = None,
    sandbox: bool = True,
    extra_args: Sequence[str] = (),
) -> str:
    """Convert a document using pandoc.

    Args:
        source: Path to the input file.
        to: Output format (default ``"markdown"``).
        from_format: Explicit input format.  If ``None``, pandoc infers
            the format from the file extension.
        sandbox: Run pandoc with ``--sandbox`` (disables filesystem
            writes).  Disable for formats that require archive access
            (DOCX, PPTX, XLSX).
        extra_args: Additional CLI arguments forwarded to pandoc.

    Returns:
        The converted document as a string.
    """
    args: list[str | Path] = ["pandoc", "--to", to]
    if from_format:
        args.extend(["--from", from_format])
    if sandbox:
        args.append("--sandbox")
    args.extend(extra_args)
    args.append(source)

    result = await run(args)
    return result.stdout_text
