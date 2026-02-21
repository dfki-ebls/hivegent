"""Pandoc-based document converter for miscellaneous document formats."""

import asyncio
from pathlib import Path

from .base import DocumentConverter

__all__ = ["PandocConverter"]

# Pandoc cannot always infer the input format from the file extension.
# Explicit format names are provided for safety even when pandoc might
# auto-detect, since the cost of an override is zero while a wrong guess
# causes a runtime error.
_FORMAT_OVERRIDES: dict[str, str] = {
    ".txt": "markdown",
    ".html": "html",
    ".xml": "html",
    ".csv": "csv",
    ".adoc": "asciidoc",
    ".odt": "odt",
    ".rst": "rst",
    ".rtf": "rtf",
    ".epub": "epub",
    ".tex": "latex",
    ".docbook": "docbook",
    ".bib": "bibtex",
    ".ris": "ris",
    ".tsv": "tsv",
    ".fb2": "fb2",
    ".opml": "opml",
    ".org": "org",
    ".ipynb": "ipynb",
    ".creole": "creole",
    ".djot": "djot",
    ".dokuwiki": "dokuwiki",
    ".jats": "jats",
    ".jira": "jira",
    ".man": "man",
    ".mediawiki": "mediawiki",
    ".muse": "muse",
    ".pod": "pod",
    ".t2t": "t2t",
    ".textile": "textile",
    ".tikiwiki": "tikiwiki",
    ".twiki": "twiki",
    ".vimwiki": "vimwiki",
    ".typst": "typst",
}

# Office formats require filesystem access (to unzip the OOXML archive)
# and cannot run in pandoc's sandbox mode.
_SANDBOX_INCOMPATIBLE = frozenset({".docx", ".pptx", ".xlsx"})


class PandocConverter(DocumentConverter):
    """Document converter using pandoc via pypandoc.

    Supports a wide range of document formats including ODT, RST, RTF, EPUB,
    LaTeX, Org-mode, DocBook, Typst, wiki markups, bibliography formats, and
    more. Also handles DOCX, PPTX, and XLSX as an alternative to the
    specialized converters (Docling, Marker, MinerU).
    """

    @property
    def name(self) -> str:
        """The unique name of this converter."""
        return "pandoc"

    def _convert_sync(self, file_path: Path) -> str:
        """Run the synchronous pypandoc conversion.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The document content as a markdown string.

        Raises:
            ImportError: If pypandoc is not installed.
            RuntimeError: If pandoc is not installed or conversion fails.
        """
        import pypandoc

        suffix = file_path.suffix.lower()
        fmt = _FORMAT_OVERRIDES.get(suffix)
        use_sandbox = suffix not in _SANDBOX_INCOMPATIBLE
        return str(
            pypandoc.convert_file(
                file_path,
                to="markdown",
                format=fmt,
                sandbox=use_sandbox,
            )
        )

    async def convert(self, file_path: Path) -> str:
        """Convert a document to markdown using pandoc.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The document content converted to markdown.

        Raises:
            ImportError: If pypandoc is not installed.
            RuntimeError: If pandoc is not installed or conversion fails.
        """
        return await asyncio.to_thread(self._convert_sync, file_path)
