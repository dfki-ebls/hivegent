"""Types and protocols for tool implementations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DocumentRange",
    "DocumentSummary",
    "GrepMatch",
    "SearchResult",
    "Tool",
]


@runtime_checkable
class Tool(Protocol):
    """Callable protocol for tool implementations.

    All tool classes must implement ``__call__`` with their specific
    signature.
    The ``__call__`` docstring serves as the canonical tool description,
    reused by agent toolsets and MCP endpoints.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Tool description — override in subclasses."""
        ...


@dataclass(slots=True, frozen=True)
class DocumentSummary:
    """Summary of a document."""

    filename: str
    size: int
    modified_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class DocumentRange:
    """A range of lines from a document."""

    start_line: int
    end_line: int
    total_lines: int
    content: str


@dataclass(slots=True, frozen=True)
class GrepMatch:
    """A pattern match in a document with a path relative to the search root."""

    filename: str
    line_number: int
    line_text: str


@dataclass(slots=True, frozen=True)
class SearchResult[K: (int, str)]:
    """A single search result with key, text, and relevance score."""

    key: K
    text: str
    score: float
