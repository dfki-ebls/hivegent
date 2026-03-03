"""Pandoc-based document converter for miscellaneous document formats."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..subprocesses import pandoc_convert
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
    """Document converter using pandoc as an async subprocess.

    Supports a wide range of document formats including ODT, RST, RTF, EPUB,
    LaTeX, Org-mode, DocBook, Typst, wiki markups, bibliography formats, and
    more. Also handles DOCX, PPTX, and XLSX as an alternative to the
    specialized converters (Docling, Marker, MinerU).
    """

    name = "pandoc"
    extensions = frozenset(_FORMAT_OVERRIDES) | _SANDBOX_INCOMPATIBLE
    config: PandocConverterConfig = field(default_factory=PandocConverterConfig)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> str:
        """Convert a document to markdown using pandoc.

        Args:
            path: Path to the document to convert.

        Returns:
            The document content converted to markdown.
        """
        suffix = path.suffix.lower()
        return await pandoc_convert(
            path,
            from_format=_FORMAT_OVERRIDES.get(suffix),
            sandbox=suffix not in _SANDBOX_INCOMPATIBLE,
            extra_args=self.config.extra_args,
        )
