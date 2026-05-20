"""Pandoc-based document converter for miscellaneous document formats."""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..subprocesses import pandoc_convert
from .base import ConversionResult, DocumentConverter, collect_dir_images

__all__ = ["PandocConverter", "PandocConverterConfig"]


class PandocConverterConfig(BaseModel):
    """Configuration for the Pandoc conversion pipeline."""

    model_config = ConfigDict(extra="forbid")


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

# These formats are zip-based containers that require filesystem access
# and cannot run in pandoc's sandbox mode.
_SANDBOX_INCOMPATIBLE = frozenset({".docx", ".pptx", ".xlsx", ".epub", ".odt"})


@dataclass(slots=True, frozen=True)
class PandocConverter(DocumentConverter):
    """Document converter using pandoc as an async subprocess.

    Supports a wide range of document formats including ODT, RST, RTF, EPUB,
    LaTeX, Org-mode, DocBook, Typst, wiki markups, bibliography formats, and
    more. Also handles DOCX, PPTX, and XLSX as an alternative to the
    specialized converters (Docling, Marker, MinerU).
    """

    name = "pandoc"
    label = "Pandoc"
    description = (
        "Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, "
        "DocBook, Typst, and more"
    )
    extensions = frozenset(_FORMAT_OVERRIDES) | _SANDBOX_INCOMPATIBLE
    config: PandocConverterConfig = field(default_factory=PandocConverterConfig)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a document to markdown using pandoc.

        Args:
            path: Path to the document to convert.

        Returns:
            The conversion result with markdown and extracted images.
        """
        suffix = path.suffix.lower()
        use_sandbox = suffix not in _SANDBOX_INCOMPATIBLE

        # Formats with embedded media benefit from --extract-media.
        if suffix in _SANDBOX_INCOMPATIBLE:
            with tempfile.TemporaryDirectory() as media_dir:
                media_path = Path(media_dir)
                markdown = await pandoc_convert(
                    path,
                    from_format=_FORMAT_OVERRIDES.get(suffix),
                    sandbox=use_sandbox,
                    extra_args=[f"--extract-media={media_path}"],
                )
                image_data = collect_dir_images(media_path, media_path)
                return ConversionResult(markdown=markdown, images=image_data)

        markdown = await pandoc_convert(
            path,
            from_format=_FORMAT_OVERRIDES.get(suffix),
            sandbox=use_sandbox,
        )
        return ConversionResult(markdown=markdown)
