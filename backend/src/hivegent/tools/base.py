"""Shared base protocol and helpers for tool implementations."""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

__all__ = [
    "Tool",
    "tool_description",
]


class Tool(ABC):
    """Abstract base class for tool implementations.

    All tool classes must implement ``__call__`` with their specific
    signature.
    The ``__call__`` docstring serves as the canonical tool description,
    reused by agent toolsets and MCP endpoints, while parameter
    descriptions live in ``Annotated`` metadata on the signature itself.
    """

    @abstractmethod
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def tool_description(tool: type[object] | Callable[..., object]) -> str | None:
    """Return the canonical user-facing description for a tool callable."""
    target = tool.__call__ if isinstance(tool, type) else tool
    return inspect.getdoc(target)
