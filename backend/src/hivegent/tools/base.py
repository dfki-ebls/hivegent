"""Shared base protocol and helpers for tool implementations."""

import inspect
import json
import re
import types
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Self, get_type_hints, override

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "AsyncPathTool",
    "AsyncTool",
    "BinaryAttachment",
    "CallInfo",
    "IncludeIgnoredArg",
    "PathTool",
    "SearchPath",
    "SearchPathFilterFunc",
    "SyncPathTool",
    "SyncTool",
    "Tool",
    "ToolOutput",
    "apply_prefix",
    "coerce_paths",
    "excluded_dirs",
    "factory_tool_name",
    "file_allowed",
    "is_in_excluded_dir",
    "resolve_accessible_file",
    "resolve_search_path",
    "resolve_tool_cls",
    "tool_description",
    "tool_name",
]

DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    "node_modules",
    ".git",
    "dist",
    "build",
    ".next",
    "coverage",
    "__pycache__",
)
"""Build and vendor directory names skipped by default in file listings."""

IncludeIgnoredArg = Annotated[
    bool,
    Field(
        description=(
            "When true, include common build and vendor directories "
            "(node_modules, .git, dist, build, .next, coverage, "
            "__pycache__) that are skipped by default."
        ),
    ),
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


def excluded_dirs(include_ignored: bool) -> tuple[str, ...]:
    """Return the default exclude list, or empty when *include_ignored* is true."""
    return () if include_ignored else DEFAULT_EXCLUDE_DIRS


def is_in_excluded_dir(rel_path: str, exclude_dirs: tuple[str, ...]) -> bool:
    """Check whether *rel_path* sits inside any excluded directory name."""
    if not exclude_dirs:
        return False
    parts = rel_path.split("/")
    return any(excluded in parts for excluded in exclude_dirs)


def resolve_accessible_file(
    paths: tuple[SearchPath, ...],
    file_path: str,
) -> tuple[SearchPath, str, Path] | None:
    """Resolve *file_path* to its search path, local name, and absolute path.

    Performs the checks every document tool shares: prefix resolution,
    filter predicate, and traversal defense against symlink escapes.
    Does *not* require the file to exist — callers that need that
    should check :py:meth:`pathlib.Path.is_file` on the returned path.
    """
    resolved = resolve_search_path(paths, file_path)
    if resolved is None:
        return None
    sp, local = resolved
    if not file_allowed(sp.filter_func, local):
        return None
    absolute = (sp.path / local).resolve()
    if not absolute.is_relative_to(sp.path.resolve()):
        return None
    return sp, local, absolute


@dataclass(slots=True, frozen=True)
class BinaryAttachment:
    """Framework-neutral binary blob attached to a tool result.

    Adapters convert it to the framework-specific representation:
    pydantic-ai ``BinaryContent`` (inline in the tool return) or MCP
    ``ImageContent`` / ``EmbeddedResource``.
    """

    data: bytes
    media_type: str
    identifier: str | None = None


class ToolOutput[T](BaseModel):
    """Tool result carrying both structured data and a compact text form.

    The adapters route ``data`` to the frontend (via the Vercel AI
    stream) and :attr:`text` to the LLM and MCP clients.

    When ``formatted`` is ``None``, :attr:`text` derives the
    representation automatically: strings are used as-is, other types
    are serialized to JSON.

    ``attachments`` carries framework-neutral binary blobs that the
    adapter converts to its framework's multimodal type and sends
    inline with the tool return.
    """

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    data: T
    formatted: str | None = None
    attachments: tuple[BinaryAttachment, ...] = ()

    @property
    def text(self) -> str:
        """Model-facing text: explicit ``formatted`` or auto-derived."""
        if self.formatted is not None:
            return self.formatted
        if isinstance(self.data, str):
            return self.data
        return json.dumps(self.data, default=str)


class Tool[T](ABC):
    """Base class for all tool implementations.

    Concrete tools should inherit from :class:`SyncTool` or
    :class:`AsyncTool` (or their :class:`PathTool` counterparts) instead
    of subclassing this directly.

    The ``__call__`` docstring serves as the canonical tool description,
    reused by agent toolsets and MCP endpoints, while parameter
    descriptions live in ``Annotated`` metadata on the signature itself.
    """

    @abstractmethod
    def __call__(
        self, *args: Any, **kwargs: Any
    ) -> ToolOutput[T] | Awaitable[ToolOutput[T]]:
        """Execute the tool."""
        ...


class SyncTool[T](Tool[T], ABC):
    """Abstract base for synchronous tool implementations."""

    @override
    @abstractmethod
    def __call__(self, *args: Any, **kwargs: Any) -> ToolOutput[T]: ...


class AsyncTool[T](Tool[T], ABC):
    """Abstract base for asynchronous tool implementations."""

    @override
    @abstractmethod
    async def __call__(self, *args: Any, **kwargs: Any) -> ToolOutput[T]: ...


@dataclass(slots=True, frozen=True)
class PathTool[T](Tool[T], ABC):
    """Tool base that owns a ``paths`` field with lazy coercion.

    Accepts a bare :class:`~pathlib.Path`, a single :class:`SearchPath`,
    or a tuple.  Use :attr:`resolved_paths` to obtain the normalised
    tuple.  Concrete subclasses should inherit from
    :class:`SyncPathTool` or :class:`AsyncPathTool`.
    """

    paths: Path | SearchPath | tuple[SearchPath, ...] = ()

    @property
    def resolved_paths(self) -> tuple[SearchPath, ...]:
        """Return *paths* normalised to a ``SearchPath`` tuple."""
        return coerce_paths(self.paths)


@dataclass(slots=True, frozen=True)
class SyncPathTool[T](PathTool[T], SyncTool[T], ABC):
    """Synchronous tool with search paths."""


@dataclass(slots=True, frozen=True)
class AsyncPathTool[T](PathTool[T], AsyncTool[T], ABC):
    """Asynchronous tool with search paths."""


def tool_name(tool_cls: type[Tool[Any]]) -> str:
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
    ``_search`` becomes ``search``.

    Args:
        factory: The factory callable.

    Returns:
        The tool name.
    """
    name: str = getattr(factory, "__name__", "") or getattr(factory, "__qualname__", "")
    return name.lstrip("_")


def resolve_tool_cls(factory: Callable[..., Tool[Any]]) -> type[Tool[Any]]:
    """Extract the Tool subclass from a factory's return type annotation.

    Handles parameterized generics like ``VectorSearchTool[str]`` by
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


def tool_description(tool: type[Tool[Any]]) -> str | None:
    """Return the canonical user-facing description for a tool callable."""
    return inspect.getdoc(tool.__call__) or inspect.getdoc(tool)


@dataclass(slots=True, frozen=True)
class CallInfo:
    """Extracted metadata from a Tool's ``__call__`` method.

    Provides the information both framework adapters need to build
    wrapper functions with correct signatures and type annotations.

    Attributes:
        name: Tool name derived from the factory function.
        description: Canonical tool description from the Tool class.
        params: ``__call__`` parameters with ``self`` removed.
        annotations: Resolved type hints for ``__call__`` parameters
            (``self`` and ``return`` excluded).
        is_async: Whether ``__call__`` is a coroutine function.
        source_module: Module name of the originating factory.
    """

    name: str
    description: str | None
    params: tuple[inspect.Parameter, ...]
    annotations: dict[str, Any]
    is_async: bool
    source_module: str

    def apply_to(
        self,
        wrapper: types.FunctionType,
        sig: inspect.Signature,
        annotations: dict[str, Any],
    ) -> None:
        """Stamp call metadata onto a wrapper function.

        Sets ``__signature__``, ``__annotations__``, ``__name__``,
        ``__qualname__``, ``__doc__``, and ``__module__`` so that
        framework introspection sees the rewritten signature.

        Args:
            wrapper: The wrapper function to decorate.
            sig: The rewritten :class:`inspect.Signature`.
            annotations: The rewritten ``__annotations__`` dict.
        """
        wrapper.__annotations__ = annotations
        wrapper.__name__ = self.name
        wrapper.__qualname__ = self.name
        wrapper.__doc__ = self.description
        wrapper.__module__ = self.source_module
        # __signature__ is not in the FunctionType stub; use setattr.
        wrapper.__signature__ = sig  # type: ignore[attr-defined]  # pyright: ignore[reportFunctionMemberAccess]  # ty: ignore[unresolved-attribute]

    @classmethod
    def from_factory(cls, factory: Callable[..., Tool[Any]]) -> Self:
        """Extract call metadata from a Tool factory's return type.

        Resolves the Tool subclass from *factory*'s return annotation,
        then inspects its ``__call__`` method to build a :class:`CallInfo`.

        Args:
            factory: A callable whose return annotation is a ``Tool``
                subclass.

        Returns:
            Extracted call information.

        Raises:
            TypeError: If the factory's return annotation is not a Tool
                subclass.
            TypeError: If any ``__call__`` parameter (besides ``self``)
                lacks a type annotation.
        """
        tool_cls = resolve_tool_cls(factory)
        call = tool_cls.__call__
        sig = inspect.signature(call)
        hints = get_type_hints(call, include_extras=True)

        params = tuple(p for name, p in sig.parameters.items() if name != "self")

        for p in params:
            if p.name not in hints:
                cls_name = tool_cls.__qualname__
                msg = f"{cls_name}.__call__ has unannotated parameter {p.name!r}"
                raise TypeError(msg)

        annotations = {
            name: hint for name, hint in hints.items() if name not in ("self", "return")
        }

        return cls(
            name=factory_tool_name(factory),
            description=tool_description(tool_cls),
            params=params,
            annotations=annotations,
            is_async=inspect.iscoroutinefunction(call),
            source_module=getattr(factory, "__module__", ""),
        )
