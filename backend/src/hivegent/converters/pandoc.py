"""Pandoc-based document converter for miscellaneous document formats."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypandoc
from pydantic import BaseModel, Field

from .base import DocumentConverter

__all__ = ["PandocConverter", "PandocConverterConfig"]


class PandocConverterConfig(BaseModel):
    """Configuration for the Pandoc conversion pipeline."""

    extra_args: list[str] = Field(
        default_factory=list,
        description="Additional pandoc CLI arguments (e.g. '--wrap=none', '--toc').",
    )


# Pandoc cannot always infer the input format from the file extension.
# Explicit format names are provided for safety even when pandoc might
# auto-detect, since the cost of an override is zero while a wrong guess
# causes a runtime error.
# https://pandoc.org/MANUAL.html#general-options (--from)
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


@dataclass(slots=True, frozen=True)
class PandocConverter(DocumentConverter):
    """Document converter using pandoc via pypandoc.

    Supports a wide range of document formats including ODT, RST, RTF, EPUB,
    LaTeX, Org-mode, DocBook, Typst, wiki markups, bibliography formats, and
    more. Also handles DOCX, PPTX, and XLSX as an alternative to the
    specialized converters (Docling, Marker, MinerU).
    """

    name = "pandoc"
    extensions = frozenset(_FORMAT_OVERRIDES) | _SANDBOX_INCOMPATIBLE

    def _convert_sync(self, path: Path, config: dict[str, Any] | None) -> str:
        """Run the synchronous pypandoc conversion."""
        parsed = PandocConverterConfig(**(config or {}))
        suffix = path.suffix.lower()
        fmt = _FORMAT_OVERRIDES.get(suffix)
        use_sandbox = suffix not in _SANDBOX_INCOMPATIBLE
        return str(
            pypandoc.convert_file(
                path,
                to="markdown",
                format=fmt,
                sandbox=use_sandbox,
                extra_args=parsed.extra_args,
            )
        )

    async def __call__(
        self,
        path: Path,
        /,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Convert a document to markdown using pandoc.

        Args:
            path: Path to the document to convert.
            config: Optional Pandoc pipeline configuration.

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path, config)
