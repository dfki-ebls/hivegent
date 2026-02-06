"""Base class for document converters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DocumentConverter", "LLMConvertOptions"]


@dataclass(frozen=True)
class LLMConvertOptions:
    """Options for LLM-based document conversion."""

    model: str = ""
    api_key: str = ""
    base_url: str | None = None


class DocumentConverter(ABC):
    """Abstract base class for document converters.

    All converters must inherit from this class and implement the required
    properties and methods. Converters are responsible for transforming
    documents from their original format into markdown.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The unique name of this converter."""
        ...

    @property
    @abstractmethod
    def supported_extensions(self) -> frozenset[str]:
        """File extensions supported by this converter (including the dot)."""
        ...

    @abstractmethod
    async def convert(self, file_path: Path) -> str:
        """Convert a document to markdown.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The document content converted to markdown.

        Raises:
            ImportError: If required dependencies are not installed.
        """
        ...
