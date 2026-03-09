"""Shared base protocol and helpers for tool implementations."""

import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, get_type_hints

__all__ = [
    "FileFilter",
    "Tool",
    "factory_tool_name",
    "file_allowed",
    "resolve_tool_cls",
    "tool_description",
    "tool_name",
]

FileFilter = Callable[[str], bool] | None
"""Optional predicate that decides whether a filename is accessible."""


def file_allowed(file_filter: FileFilter, filename: str) -> bool:
    """Return whether *filename* passes *file_filter*.

    Args:
        file_filter: A predicate, or ``None`` to allow everything.
        filename: The relative document path to check.

    Returns:
        ``True`` if the file is allowed, ``False`` otherwise.
    """
    return file_filter is None or file_filter(filename)


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


def tool_name(tool_cls: type[Tool]) -> str:
    """Derive a snake_case tool name from a CamelCase class name.

    Strips a trailing ``Tool`` suffix so that e.g.
    ``ListDocumentsTool`` becomes ``list_documents``.

    Args:
        tool_cls: The tool class.

    Returns:
        The snake_case name.
    """
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", tool_cls.__name__)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower().removesuffix("_tool")


def factory_tool_name(factory: Callable[..., Any]) -> str:
    """Derive a tool name from a factory function.

    Strips a leading underscore from the function name so that
    ``_semantic_search`` becomes ``semantic_search``.

    Args:
        factory: The factory callable.

    Returns:
        The tool name.
    """
    name: str = getattr(factory, "__name__", "") or getattr(factory, "__qualname__", "")
    return name.lstrip("_")


def resolve_tool_cls(factory: Callable[..., Tool]) -> type[Tool]:
    """Extract the Tool subclass from a factory's return type annotation.

    Handles parameterized generics like ``LanceDBSearchTool[str]`` by
    unwrapping via ``__origin__``.

    Args:
        factory: A callable whose return annotation is a ``Tool`` subclass.

    Returns:
        The Tool subclass.

    Raises:
        TypeError: If the return annotation is missing or not a Tool subclass.
    """
    hints = get_type_hints(factory)
    tool_cls = hints.get("return")
    origin = getattr(tool_cls, "__origin__", None)
    if origin is not None:
        tool_cls = origin
    if not isinstance(tool_cls, type) or not issubclass(tool_cls, Tool):
        name = getattr(factory, "__qualname__", repr(factory))
        msg = (
            f"{name!r} must have a return type "
            f"annotation that is a Tool subclass, got {tool_cls!r}"
        )
        raise TypeError(msg)
    return tool_cls


def tool_description(tool: type[Tool]) -> str | None:
    """Return the canonical user-facing description for a tool callable."""
    return inspect.getdoc(tool.__call__) or inspect.getdoc(tool)
