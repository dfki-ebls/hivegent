"""Base class for document converters."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

__all__ = ["DocumentConverter"]


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
    ) -> str:
        """Convert a document to markdown.

        Args:
            path: Path to the document to convert.

        Returns:
            The document content converted to markdown.
        """
        ...
