"""What each file format is, by suffix, shared by every layer that reads or writes files.

Part of the dependency-free `shared` package, so the self-contained `tools`
package, the pipelines, and the app agree on one table per question rather than each keeping
its own.
"""

from collections.abc import Set as AbstractSet
from enum import StrEnum
from mimetypes import MimeTypes
from pathlib import Path, PurePath

from exar.ingest.model import VISION_MEDIA_TYPES
from exar.tools.formats import DELIMITERS, TABULAR_SUFFIXES

__all__ = [
    "BINARY_SUFFIXES",
    "BINARY_WRITE_REASON",
    "DELIMITED_SUFFIXES",
    "DELIMITERS",
    "DOCUMENT_EXTENSION",
    "IMAGE_EXTENSIONS",
    "IMAGE_MEDIA_TYPES",
    "INGESTIBLE_IMAGE_MEDIA_TYPES",
    "JSON_SUFFIXES",
    "LLM_MEDIA_TYPES",
    "TABULAR_SUFFIXES",
    "VIDEO_MEDIA_TYPES",
    "ZIP_CONTAINER_EXTENSIONS",
    "BinaryContentMode",
    "EntryProjection",
    "is_image_suffix",
    "is_json",
    "is_markdown_suffix",
    "is_tabular",
    "is_textual_media_type",
    "match_file_extension",
    "media_type",
    "projection_for",
    "writes_as_text",
]


class BinaryContentMode(StrEnum):
    """How binary content reaches the chat model.

    The agent's binary reader can carry images, PDFs, and video.  This
    policy selects the representation:

    - :attr:`IMAGES` rasterises PDFs to one image per page, the only
      multimodal content type OpenAI-compatible vision servers (vLLM,
      SGLang, ...) accept.  They reject the native ``file`` part outright.
    - :attr:`NATIVE` forwards PDF bytes with their ``application/pdf``
      media type, for providers with first-class document understanding
      (OpenAI, Anthropic) that ingest ``file`` parts directly.

    Images are always sent as images and time-based media (video,
    animations) is always sampled to frames either way, because no chat
    model ingests those containers natively.
    """

    IMAGES = "images"
    NATIVE = "native"


# All converted documents are stored as markdown.
DOCUMENT_EXTENSION = ".md"

IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".ico": "image/vnd.microsoft.icon",
}
"""Extension → media type for every image format recognised here.

Declared rather than looked up so the answer cannot vary with whichever
``mime.types`` registry the host happens to ship, and so the recognised
extensions and their media types cannot drift apart.  Which of these a
chat model can actually be shown is a separate, narrower question; see
:data:`exar.ingest.VISION_MEDIA_TYPES`.
"""

IMAGE_EXTENSIONS = frozenset(IMAGE_MEDIA_TYPES)

VIDEO_MEDIA_TYPES: dict[str, str] = {
    suffix: kind
    for suffix, kind in VISION_MEDIA_TYPES.items()
    if kind.startswith("video/")
}
"""Extension → media type for the supported video containers, those a vision model is shown frames of."""


def is_markdown_suffix(suffix: str) -> bool:
    """Return whether *suffix* is the markdown document extension, spelled as :func:`~hivegent.config.sanitize_document_path` canonicalizes it.

    >>> is_markdown_suffix(".md"), is_markdown_suffix(".MD")
    (True, False)
    """
    return suffix == DOCUMENT_EXTENSION


def is_image_suffix(suffix: str) -> bool:
    """Return whether *suffix* matches a known image extension."""
    return suffix.lower() in IMAGE_EXTENSIONS


def _is_video_suffix(suffix: str) -> bool:
    """Return whether *suffix* matches a supported video extension."""
    return suffix.lower() in VIDEO_MEDIA_TYPES


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

The image half is composed from :data:`IMAGE_MEDIA_TYPES`
so the two cannot drift apart.
"""

ZIP_CONTAINER_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx", ".epub", ".odt"})
"""Zip-based document containers, binary however their parts are written."""

_ARCHIVE_EXTENSIONS = frozenset(
    {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar"}
)
"""Archives, which pack files rather than being text, a collection upload's ``.zip`` among them."""


class EntryProjection(StrEnum):
    """How an entry's markdown is derived from the file it is derived from."""

    MARKDOWN = "markdown"
    """The file is its own description; nothing is derived."""

    IMAGE = "image"
    VIDEO = "video"
    """Derived by a vision model, from the still or from sampled frames."""

    CONVERTIBLE = "convertible"
    """Derived by a converter, or copied verbatim when that converter is plain text."""


_TEXT_IMAGE_SUFFIXES: frozenset[str] = frozenset({".svg"})
"""Image formats that are markup, so readers serve them and a text write creates them as text."""


def projection_for(filename: str) -> EntryProjection:
    """Return which projection *filename* gets, by extension.

    The one routing table for every path that turns a file into an entry.  An
    SVG is converted, since a vision model cannot be shown one.

    >>> projection_for("diagram.svg") is EntryProjection.CONVERTIBLE
    True
    >>> projection_for("photo.png") is EntryProjection.IMAGE
    True
    """
    if is_markdown_suffix(Path(filename).suffix):
        return EntryProjection.MARKDOWN

    suffix = Path(filename).suffix.lower()
    if is_image_suffix(suffix) and suffix not in _TEXT_IMAGE_SUFFIXES:
        return EntryProjection.IMAGE
    if _is_video_suffix(suffix):
        return EntryProjection.VIDEO
    return EntryProjection.CONVERTIBLE


_INGESTIBLE_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
"""Image formats a chat model accepts verbatim, the subset vision APIs agree on."""

INGESTIBLE_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    IMAGE_MEDIA_TYPES[ext] for ext in _INGESTIBLE_IMAGE_EXTENSIONS
)
"""Media types a chat model ingests verbatim as an image, for the file picker and the chat route alike."""


DELIMITED_SUFFIXES: frozenset[str] = frozenset(DELIMITERS)
"""The text half of :data:`TABULAR_SUFFIXES`, which a text write may create."""


JSON_SUFFIXES: frozenset[str] = frozenset({".json"})
"""Extensions a jq filter can be run against in place, ``.jsonl`` left out since jq takes one document."""


BINARY_SUFFIXES: frozenset[str] = (
    (frozenset(IMAGE_MEDIA_TYPES) - _TEXT_IMAGE_SUFFIXES)
    | frozenset(VIDEO_MEDIA_TYPES)
    | frozenset(LLM_MEDIA_TYPES)
    | ZIP_CONTAINER_EXTENSIONS
    | (TABULAR_SUFFIXES - DELIMITED_SUFFIXES)
    | _ARCHIVE_EXTENSIONS
)
"""Extensions whose bytes are not text, which a text write refuses to create.

Composed from the tables that name a binary format, so a suffix none claims
is created as the text written to it.
"""

BINARY_WRITE_REASON = (
    "is a binary format, which a text write cannot create: write the text to a "
    "'.md' path instead, or upload the file"
)
"""Shared wording for a rejected write, the counterpart of `exar.text.NOT_TEXT_REASON`."""


def is_tabular(file_path: str) -> bool:
    """Return whether *file_path* names a table a SQL query can be run against."""
    return Path(file_path).suffix.lower() in TABULAR_SUFFIXES


def is_json(file_path: str) -> bool:
    """Return whether *file_path* names a document a jq filter can be run against."""
    return Path(file_path).suffix.lower() in JSON_SUFFIXES


def writes_as_text(filename: str) -> bool:
    """Return whether *filename* can be created by writing text at it.

    A file that does not exist yet has no bytes to decode, so creation decides
    by :data:`BINARY_SUFFIXES`.

    >>> writes_as_text("report.csv")
    True
    >>> writes_as_text("notes/report.docx")
    False
    >>> writes_as_text("figure.svg")
    True
    >>> writes_as_text("figure.png")
    False
    >>> writes_as_text("Makefile")
    True
    """
    return Path(filename).suffix.lower() not in BINARY_SUFFIXES


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
