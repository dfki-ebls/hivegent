"""Dependency-free format policy shared by routing and converters."""

from collections.abc import Set as AbstractSet
from mimetypes import MimeTypes
from pathlib import PurePath

from .base import IMAGE_MEDIA_TYPES, VIDEO_MEDIA_TYPES

__all__ = [
    "ANYDOC_EXTENSIONS",
    "DOCLING_EXTENSIONS",
    "KREUZBERG_MARKUP_EXTENSIONS",
    "LLM_MEDIA_TYPES",
    "PANDOC_EXTENSIONS",
    "PANDOC_FORMAT_OVERRIDES",
    "PANDOC_SANDBOX_INCOMPATIBLE",
    "PLAIN_TEXT_EXTENSIONS",
    "is_textual_media_type",
    "match_file_extension",
    "media_type",
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

# https://github.com/firecrawl/anydoc#supported-formats, minus ``.pdf`` (see
# AnydocConverter's docstring for why).  It reads templates by their content.
# AUTO prefers docling where both apply, so anydoc takes the legacy Office, RTF
# and OpenDocument formats docling is not given (see `docling.py`).
ANYDOC_EXTENSIONS = frozenset(
    {
        ".csv",
        ".doc",
        ".docm",
        ".docx",
        ".dot",
        ".epub",
        ".odp",
        ".ods",
        ".odt",
        ".otp",
        ".ots",
        ".ott",
        ".pot",
        ".pps",
        ".ppsm",
        ".ppsx",
        ".ppt",
        ".pptm",
        ".pptx",
        ".rtf",
        ".xls",
        ".xlsb",
        ".xlsm",
        ".xlsx",
        ".xlt",
    }
)

# Markup kreuzberg reads natively, which AUTO converts with it.
KREUZBERG_MARKUP_EXTENSIONS = frozenset(
    {
        ".djot",
        ".docbook",
        ".fb2",
        ".ipynb",
        ".jats",
        ".opml",
        ".org",
        ".rst",
        ".tsv",
        ".typ",
        ".typst",
    }
)

# Stable application contract derived from Docling's FormatToExtensions, copied
# rather than imported so routing stays dependency-free, minus the formats it
# cannot read with what the project ships (see `docling.py`).
# https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py
DOCLING_EXTENSIONS = frozenset(
    {
        ".adoc",
        ".afp",
        ".asc",
        ".asciidoc",
        ".bmp",
        ".boxnote",
        ".csv",
        ".dclg",
        ".dclg.xml",
        ".dclx",
        ".docm",
        ".docx",
        ".dotm",
        ".dotx",
        ".ebc",
        ".ebcdic",
        ".eml",
        ".epub",
        ".gif",
        ".htm",
        ".html",
        ".jpeg",
        ".jpg",
        ".json",
        ".key",
        ".latex",
        ".markdown",
        ".md",
        ".mht",
        ".mhtml",
        ".msg",
        ".numbers",
        ".nxml",
        ".pages",
        ".pdf",
        ".png",
        ".potm",
        ".potx",
        ".ppsm",
        ".ppsx",
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
        ".webp",
        ".xbrl",
        ".xhtml",
        ".xlsm",
        ".xlsx",
        ".xltm",
        ".xltx",
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


def _media_types() -> MimeTypes:
    """Python's own suffix table, overridden by the declared ones.

    Unlike the host ``mime.types`` files the module functions merge in, it is
    the same on every host, and it does not mislabel common source files
    (``.rs`` as an XML service description, ``.ts`` as a Qt translation).  The
    formats documents embed that it lacks are named as IANA and mailcap do.
    """
    types = MimeTypes()
    declared = {
        ".emf": "image/emf",
        ".jp2": "image/jp2",
        ".jpx": "image/jpx",
        ".jxl": "image/jxl",
        ".psd": "image/vnd.adobe.photoshop",
        ".tga": "image/x-targa",
        ".wmf": "image/wmf",
        **LLM_MEDIA_TYPES,
        **IMAGE_MEDIA_TYPES,
        **VIDEO_MEDIA_TYPES,
    }

    for suffix, media in declared.items():
        types.add_type(media, suffix)

    return types


_MEDIA_TYPES = _media_types()


def media_type(path: str) -> str | None:
    """Return the media type *path*'s suffix names, or ``None`` when it names none.

    The one resolver behind every stored, served, and shown media type.  A
    source or config format without an entry stays ``None``, so a caller
    decides from the bytes rather than from a wrong label.

    >>> media_type("figure.SVG")
    'image/svg+xml'
    >>> media_type("clip.mkv")
    'video/x-matroska'
    >>> media_type("notes.md")
    'text/markdown'
    >>> media_type("scan.jp2")
    'image/jp2'
    >>> media_type("main.rs") is None
    True
    """
    return _MEDIA_TYPES.guess_file_type(path)[0]


def is_textual_media_type(media_type: str) -> bool:
    """Return whether *media_type* names a textual format.

    >>> is_textual_media_type("application/ld+json")
    True
    >>> is_textual_media_type("image/png")
    False
    """
    return (
        media_type.startswith("text/")
        or media_type in ("application/json", "application/xml")
        or media_type.endswith(("+json", "+xml"))
    )
