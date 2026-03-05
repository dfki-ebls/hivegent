"""Base class for document converters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from io import BytesIO

from PIL import Image

__all__ = [
    "ConversionResult",
    "DocumentConverter",
    "collect_dir_images",
    "pil_to_png_bytes",
]


@dataclass(slots=True, frozen=True)
class ConversionResult:
    """Result of converting a document to markdown.

    Attributes:
        markdown: The converted markdown content.
        images: Mapping of relative image paths (as referenced in the
            markdown) to their binary content.
    """

    markdown: str
    images: dict[str, bytes] = field(default_factory=dict)


def pil_to_png_bytes(img: Image.Image) -> bytes:
    """Serialize a PIL image to PNG bytes.

    Args:
        img: A PIL ``Image`` object.

    Returns:
        PNG-encoded bytes.
    """
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def collect_dir_images(root: Path, relative_to: Path) -> dict[str, bytes]:
    """Collect all files under *root* as a mapping of POSIX-relative paths to bytes.

    Args:
        root: Directory to scan recursively.
        relative_to: Base path for computing relative keys.

    Returns:
        Mapping of relative POSIX paths to file contents.
    """
    result: dict[str, bytes] = {}
    if not root.exists():
        return result
    for f in root.rglob("*"):
        if f.is_file():
            result[str(f.relative_to(relative_to).as_posix())] = f.read_bytes()
    return result


class DocumentConverter(ABC):
    """Abstract base class for document converters.

    All converters must inherit from this class and implement the required
    methods. Converters are responsible for transforming documents from their
    original format into markdown.

    Subclasses must define the ``name`` and ``extensions`` class variables.
    """

    name: ClassVar[str]
    extensions: ClassVar[frozenset[str]]

    @abstractmethod
    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a document to markdown.

        Args:
            path: Path to the document to convert.

        Returns:
            The conversion result with markdown and optional extracted images.
        """
        ...
