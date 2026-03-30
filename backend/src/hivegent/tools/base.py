"""Shared base protocol and helpers for tool implementations."""

import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints

__all__ = [
    "PathsTool",
    "SearchPath",
    "SearchPathFilterFunc",
    "Tool",
    "apply_prefix",
    "coerce_paths",
    "factory_tool_name",
    "file_allowed",
    "resolve_search_path",
    "resolve_tool_cls",
    "tool_description",
    "tool_name",
]

SearchPathFilterFunc = Callable[[str], bool] | None
"""Optional predicate that decides whether a filename is accessible."""


def apply_prefix(prefix: str | None, name: str) -> str:
    """Prepend *prefix* to *name* with a ``/`` separator.

    Returns *name* unchanged when *prefix* is ``None``.
    """
    return f"{prefix}/{name}" if prefix is not None else name


@dataclass(slots=True, frozen=True)
class SearchPath:
    """A labeled directory path with optional filter.

    Groups multiple search roots under a common interface.
    Each path may carry a display *prefix* so that results from
    different roots remain distinguishable, and an optional
    *filter_func* that controls which files are visible.

    Attributes:
        path: Filesystem path to the directory to search.
        prefix: Display prefix prepended to filenames from this path.
            ``None`` means no prefix.
        filter_func: Optional predicate controlling file visibility.
    """

    path: Path
    prefix: str | None = None
    filter_func: SearchPathFilterFunc = None

    def prefixed(self, filename: str) -> str:
        """Return *filename* with this path's prefix prepended."""
        return apply_prefix(self.prefix, filename)


def coerce_paths(
    raw: Path | SearchPath | tuple[SearchPath, ...],
) -> tuple[SearchPath, ...]:
    """Normalise into a ``SearchPath`` tuple.

    Accepts a bare :class:`~pathlib.Path`, a single :class:`SearchPath`,
    or an already-normalised tuple.
    """
    if isinstance(raw, Path):
        return (SearchPath(path=raw),)
    if isinstance(raw, SearchPath):
        return (raw,)
    return raw


def resolve_search_path(
    paths: tuple[SearchPath, ...],
    filename: str,
) -> tuple[SearchPath, str] | None:
    """Find the :class:`SearchPath` matching a possibly-prefixed filename.

    Prefixed paths are checked first.  If the filename does not match
    any prefix, the first path with an empty prefix is used as the
    default.

    Args:
        paths: Ordered search paths to check.
        filename: A filename that may carry a prefix.

    Returns:
        ``(search_path, local_filename)`` with the prefix stripped,
        or ``None`` if no path matches.
    """
    default: SearchPath | None = None
    for sp in paths:
        if sp.prefix is not None:
            tag = f"{sp.prefix}/"
            if filename.startswith(tag):
                return sp, filename[len(tag) :]
        elif default is None:
            default = sp
    if default is not None:
        return default, filename
    return None


def file_allowed(filter_func: SearchPathFilterFunc, filename: str) -> bool:
    """Return whether *filename* passes *filter_func*.

    Args:
        filter_func: A predicate, or ``None`` to allow everything.
        filename: The relative document path to check.

    Returns:
        ``True`` if the file is allowed, ``False`` otherwise.
    """
    return filter_func is None or filter_func(filename)


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


@dataclass(slots=True, frozen=True)
class PathsTool(Tool, ABC):
    """Tool base that owns a ``paths`` field with lazy coercion.

    Accepts a bare :class:`~pathlib.Path`, a single :class:`SearchPath`,
    or a tuple.  Use :attr:`resolved_paths` to obtain the normalised
    tuple.
    """

    paths: Path | SearchPath | tuple[SearchPath, ...] = ()

    @property
    def resolved_paths(self) -> tuple[SearchPath, ...]:
        """Return *paths* normalised to a ``SearchPath`` tuple."""
        return coerce_paths(self.paths)


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
