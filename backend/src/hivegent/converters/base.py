"""Base helpers and shared constants for document converters."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from io import BytesIO
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import urlsplit

__all__ = [
    "DOCUMENT_EXTENSION",
    "IMAGE_EXTENSIONS",
    "AssetBBox",
    "AssetRole",
    "BinaryContentMode",
    "ConversionResult",
    "DocumentConverter",
    "ExtractedImage",
    "collect_dir_images",
    "decode_text",
    "is_external_ref",
    "is_image_suffix",
    "is_markdown_suffix",
    "pil_to_png_bytes",
]


class BinaryContentMode(StrEnum):
    """How binary content reaches the chat model.

    The agent's binary reader and ad-hoc chat attachments can carry
    images, PDFs, and video.  This policy selects the representation:

    - :attr:`IMAGES` rasterises PDFs to one image per page, the only
      multimodal content type OpenAI-compatible vision servers (vLLM,
      SGLang, ...) accept — they reject the native ``file`` part outright.
    - :attr:`NATIVE` forwards PDF bytes with their ``application/pdf``
      media type, for providers with first-class document understanding
      (OpenAI, Anthropic) that ingest ``file`` parts directly.

    Images are always sent as images and time-based media (video,
    animations) is always sampled to frames either way, because no chat
    model ingests those containers natively.
    """

    IMAGES = "images"
    NATIVE = "native"


# URL schemes whose references resolve to a valid, fetchable resource; any
# other scheme (``file:``, a Windows drive letter like ``T:``, ...) points off
# the workspace.
_SERVABLE_SCHEMES = frozenset({"http", "https", "data"})


# All converted documents are stored as markdown.
DOCUMENT_EXTENSION = ".md"

IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".tiff",
        ".tif",
        ".ico",
    }
)


def is_markdown_suffix(suffix: str) -> bool:
    """Return whether *suffix* matches the markdown document extension."""
    return suffix.lower() == DOCUMENT_EXTENSION


def is_image_suffix(suffix: str) -> bool:
    """Return whether *suffix* matches a known image extension."""
    return suffix.lower() in IMAGE_EXTENSIONS


def decode_text(content: bytes) -> str | None:
    """Return *content* decoded as UTF-8 text, or ``None`` if it looks binary.

    A NUL byte is both the strongest binary signal and illegal in a PostgreSQL
    ``text`` column, so its presence rejects the content even when the
    remaining bytes would decode. This is the content-based gate used to index
    arbitrary plain text (JSON, logs, source, extension-less files) as-is
    rather than discarding it behind a metadata-only stub.

    >>> decode_text(b'{"a": 1}')
    '{"a": 1}'
    >>> decode_text(b"\\x89PNG\\r\\n") is None
    True
    """
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_external_ref(ref: str) -> bool:
    """Return whether *ref* points outside the workspace and cannot be served.

    Converters sometimes leak the author's original, unreachable image
    location into the markdown: an absolute filesystem path, a Windows drive
    path (``T:\\...``), a ``file:`` URI, or a backslash path. Such references
    can never resolve against the workspace, so callers strip them. Web
    (``http(s)://``) and inline (``data:``) sources are external but valid and
    are therefore not flagged.

    >>> is_external_ref("media/image1.png")
    False
    >>> is_external_ref(r"T:\\grafik\\logoad.jpg")
    True
    >>> is_external_ref("/var/folders/tmp/media/image65.jpg")
    True
    >>> is_external_ref("https://example.com/a.png")
    False
    """
    ref = ref.strip()
    if not ref:
        return False
    if scheme := urlsplit(ref).scheme:
        return scheme not in _SERVABLE_SCHEMES
    # Schemeless references are servable only as relative POSIX paths; an
    # absolute root or a Windows separator cannot resolve inside the workspace.
    return ref.startswith("/") or "\\" in ref


class _PngSerializable(Protocol):
    """Image-like object that can serialize itself into PNG bytes."""

    def save(self, fp: BytesIO, format: str) -> object:
        """Write the image to a file-like object."""


class AssetRole(str, Enum):
    """Normalized semantic role of an extracted asset.

    Converter-agnostic: each converter's driver maps its native category
    labels (Docling's ``PictureClassificationLabel``, Kreuzberg's
    ``is_mask``, …) onto these roles.  Downstream consumers — the
    asset-triage layer, the UI, retrieval filters — operate on the
    normalized role and never see the raw labels.  When a converter
    has no opinion the role stays :attr:`UNKNOWN` and the triage layer
    falls through to byte-level heuristics.
    """

    DECORATIVE = "decorative"
    """Icons, logos, signatures, stamps, page chrome, masks — rarely worth describing."""

    INFORMATIVE = "informative"
    """Charts, diagrams, photographs, screenshots, maps — usually worth describing."""

    UNKNOWN = "unknown"
    """Converter has no semantic signal for this asset."""


@dataclass(slots=True, frozen=True)
class AssetBBox:
    """Bounding box of an extracted asset on its source page.

    Coordinates are normalized to ``[0, 1]`` along each axis so converters'
    native units (PDF points, pixels, …) collapse onto one representation.
    The origin ``(0, 0)`` is the top-left corner of the page; ``x`` grows
    right, ``y`` grows down.
    """

    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(slots=True, frozen=True)
class ExtractedImage:
    """Asset extracted from a converted document.

    ``data`` is the raw image bytes; remaining fields are populated on a
    best-effort basis by individual converters.  Converters that lack a
    particular signal simply leave the corresponding field at its
    default.
    """

    data: bytes
    role: AssetRole = AssetRole.UNKNOWN
    bbox: AssetBBox | None = None
    page_no: int | None = None
    caption: str | None = None


@dataclass(slots=True, frozen=True)
class ConversionResult:
    """Result of converting a document to markdown.

    Attributes:
        markdown: The converted markdown content.
        images: Mapping of relative image paths (as referenced in the
            markdown) to per-asset metadata.
    """

    markdown: str
    images: dict[str, ExtractedImage] = field(default_factory=dict)


def pil_to_png_bytes(img: _PngSerializable) -> bytes:
    """Serialize a PIL image to PNG bytes.

    Args:
        img: A PIL ``Image`` object.

    Returns:
        PNG-encoded bytes.
    """
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def collect_dir_images(root: Path, relative_to: Path) -> dict[str, ExtractedImage]:
    """Collect all files under *root* as a mapping of POSIX-relative paths to ExtractedImage.

    Args:
        root: Directory to scan recursively.
        relative_to: Base path for computing relative keys.

    Returns:
        Mapping of relative POSIX paths to extracted-image entries (bytes
        only — converters that have no further metadata get the default).
    """
    result: dict[str, ExtractedImage] = {}
    if not root.exists():
        return result
    for f in root.rglob("*"):
        if f.is_file():
            result[str(f.relative_to(relative_to).as_posix())] = ExtractedImage(
                data=f.read_bytes()
            )
    return result


@dataclass(slots=True, frozen=True)
class DocumentConverter(ABC):
    """Abstract base class for document converters.

    Subclasses implement :meth:`_convert`; the base :meth:`__call__`
    acquires a single process-wide :class:`asyncio.Lock` so concurrent
    invocations of any converter (docling, marker, markitdown, chonkie
    chefs, …) cannot race on shared cached instances.  Conversion is
    bottlenecked by model loading and disk I/O, so global serialization
    is cheap.  Native-async converters (kreuzberg, llm, pandoc) pay a
    negligible uncontended lock acquire.
    """

    name: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    extensions: ClassVar[frozenset[str]]
    _invoke_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    detect_asset_roles: bool = field(default=False, kw_only=True)
    """Whether to compute :class:`AssetRole` signals for extracted assets.

    The roles only feed the asset-triage layer, which runs solely in
    DESCRIBE asset mode; when ``False``, converters may skip the work of
    producing them (e.g. docling's picture classifier).
    """

    @abstractmethod
    async def _convert(self, path: Path, /) -> ConversionResult:
        """Convert a document to markdown.

        Args:
            path: Path to the document to convert.

        Returns:
            The conversion result with markdown and optional extracted images.
        """
        ...

    async def __call__(self, path: Path, /) -> ConversionResult:
        """Acquire the per-type invocation lock and call :meth:`_convert`."""
        async with self._invoke_lock:
            return await self._convert(path)
