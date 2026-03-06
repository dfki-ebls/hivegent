"""Types and protocols for tool implementations."""

import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from collections.abc import Callable

__all__ = [
    "DocumentRange",
    "DocumentSummary",
    "GrepMatch",
    "SearchResult",
    "Tool",
    "tool_description",
]


@runtime_checkable
class Tool(Protocol):
    """Callable protocol for tool implementations.

    All tool classes must implement ``__call__`` with their specific
    signature.
    The ``__call__`` docstring serves as the canonical tool description,
    reused by agent toolsets and MCP endpoints, while parameter
    descriptions live in ``Annotated`` metadata on the signature itself.
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


def tool_description(tool: type[object] | Callable[..., object]) -> str | None:
    """Return the canonical user-facing description for a tool callable."""
    target = tool.__call__ if isinstance(tool, type) else tool
    return inspect.getdoc(target)
