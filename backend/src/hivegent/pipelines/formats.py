"""The formats each built-in converter reads, declared for routing without importing it."""

from ..shared.formats import ZIP_CONTAINER_EXTENSIONS

__all__ = [
    "ANYDOC_EXTENSIONS",
    "DOCLING_EXTENSIONS",
    "KREUZBERG_EXTENSIONS",
    "KREUZBERG_MARKUP_EXTENSIONS",
    "MARKITDOWN_EXTENSIONS",
    "PANDOC_EXTENSIONS",
    "PANDOC_FORMAT_OVERRIDES",
    "PLAIN_TEXT_EXTENSIONS",
]

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

PANDOC_EXTENSIONS = frozenset(PANDOC_FORMAT_OVERRIDES) | ZIP_CONTAINER_EXTENSIONS
PLAIN_TEXT_EXTENSIONS = frozenset({".json", ".text", ".txt", ".xml"})
# https://github.com/firecrawl/anydoc#supported-formats, minus ``.pdf`` (see
# AnydocConverter's docstring for why).  It reads templates by their content,
# but not the legacy `.dot`, `.pot`, and `.xlt` ones, since text formats such
# as Graphviz and gettext share those suffixes and AUTO routes by suffix.
# AUTO prefers docling where both apply, so anydoc takes the legacy Office, RTF
# and OpenDocument formats docling is not given (see `docling.py`).
ANYDOC_EXTENSIONS = frozenset(
    {
        ".csv",
        ".doc",
        ".docm",
        ".docx",
        ".epub",
        ".odp",
        ".ods",
        ".odt",
        ".otp",
        ".ots",
        ".ott",
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
        ".ical",
        ".icalendar",
        ".ics",
        ".ifb",
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

# Kreuzberg has no API to enumerate its formats at once.
# https://docs.kreuzberg.dev/features/supported-formats/
KREUZBERG_EXTENSIONS = (
    frozenset(
        {
            ".pdf",
            ".docx",
            ".xlsx",
            ".pptx",
            ".doc",
            ".xls",
            ".ppt",
            ".odt",
            ".ods",
            ".html",
            ".htm",
            ".xml",
            ".json",
            ".csv",
            ".epub",
            ".rtf",
            ".txt",
            ".md",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".tiff",
            ".tif",
            ".bmp",
            ".svg",
            ".ico",
            ".msg",
            ".eml",
            ".zip",
            ".tar",
            ".gz",
            ".7z",
        }
    )
    | KREUZBERG_MARKUP_EXTENSIONS
)

# MarkItDown has no public format listing, each of its converters declares its own extensions.
# https://github.com/microsoft/markitdown/tree/main/packages/markitdown/src/markitdown/converters
MARKITDOWN_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".xlsx",
        ".xls",
        ".pptx",
        ".html",
        ".htm",
        ".csv",
        ".json",
        ".jsonl",
        ".ndjson",
        ".xml",
        ".rss",
        ".atom",
        ".epub",
        ".ipynb",
        ".zip",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".msg",
    }
)
