"""Base helpers and shared constants for document converters."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import ClassVar, Protocol

__all__ = [
    "DOCUMENT_EXTENSION",
    "IMAGE_EXTENSIONS",
    "AssetBBox",
    "AssetRole",
    "ConversionResult",
    "DocumentConverter",
    "ExtractedImage",
    "collect_dir_images",
    "is_image_suffix",
    "is_markdown_suffix",
    "pil_to_png_bytes",
]


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
