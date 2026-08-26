"""Dependency-free format policy shared by routing and converters."""

from collections.abc import Set as AbstractSet
from pathlib import PurePath

from .base import IMAGE_MEDIA_TYPES

__all__ = [
    "DOCLING_EXTENSIONS",
    "LLM_MEDIA_TYPES",
    "PANDOC_EXTENSIONS",
    "PANDOC_FORMAT_OVERRIDES",
    "PANDOC_SANDBOX_INCOMPATIBLE",
    "PLAIN_TEXT_EXTENSIONS",
    "match_file_extension",
]


_LLM_IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
)
"""Raster formats a vision model reads directly, excluding SVG and ICO."""

LLM_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    **{ext: IMAGE_MEDIA_TYPES[ext] for ext in _LLM_IMAGE_EXTENSIONS},
}
"""Extension → media type the LLM converter sends as ``BinaryContent``.

The image half is composed from :data:`~hivegent.converters.base.IMAGE_MEDIA_TYPES`
so the two cannot drift apart.
"""


# Pandoc cannot always infer the input format from the file extension.
# Explicit format names are provided for safety even when pandoc might
# auto-detect, since the cost of an override is zero while a wrong guess
# causes a runtime error.
# https://pandoc.org/MANUAL.html#general-options (--from)
PANDOC_FORMAT_OVERRIDES: dict[str, str] = {
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

# Zip-based containers that require filesystem access and so cannot run in
# pandoc's sandbox mode.
PANDOC_SANDBOX_INCOMPATIBLE = frozenset({".docx", ".pptx", ".xlsx", ".epub", ".odt"})
PANDOC_EXTENSIONS = frozenset(PANDOC_FORMAT_OVERRIDES) | PANDOC_SANDBOX_INCOMPATIBLE
PLAIN_TEXT_EXTENSIONS = frozenset({".json", ".text", ".txt", ".xml"})

# Stable application contract derived from Docling's FormatToExtensions, copied
# rather than imported so routing stays dependency-free.
# https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py
DOCLING_EXTENSIONS = frozenset(
    {
        ".aac",
        ".adoc",
        ".asc",
        ".asciidoc",
        ".avi",
        ".bmp",
        ".boxnote",
        ".csv",
        ".dclg",
        ".dclg.xml",
        ".dclx",
        ".doc",
        ".docm",
        ".docx",
        ".dot",
        ".dotm",
        ".dotx",
        ".ebc",
        ".ebcdic",
        ".eml",
        ".epub",
        ".flac",
        ".htm",
        ".html",
        ".jpeg",
        ".jpg",
        ".json",
        ".latex",
        ".m4a",
        ".md",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".msg",
        ".nxml",
        ".odp",
        ".ods",
        ".odt",
        ".ogg",
        ".otp",
        ".ots",
        ".ott",
        ".pages",
        ".pdf",
        ".png",
        ".pot",
        ".potm",
        ".potx",
        ".pps",
        ".ppsm",
        ".ppsx",
        ".ppt",
        ".pptm",
        ".pptx",
        ".qmd",
        ".rmd",
        ".tar.gz",
        ".tex",
        ".text",
        ".tif",
        ".tiff",
        ".txt",
        ".vtt",
        ".wav",
        ".webm",
        ".webp",
        ".xbrl",
        ".xhtml",
        ".xls",
        ".xlsm",
        ".xlsx",
        ".xlt",
        ".xml",
    }
)


def match_file_extension(filename: str, extensions: AbstractSet[str]) -> str:
    """Return the longest registered extension matching *filename*."""
    suffixes = PurePath(filename).suffixes
    for index in range(len(suffixes)):
        extension = "".join(suffixes[index:]).casefold()
        if extension in extensions:
            return extension

    return suffixes[-1].casefold() if suffixes else ""
