"""Shared base protocol and helpers for tool implementations."""

import inspect
import json
import re
import types
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from os import stat_result
from pathlib import Path
from stat import S_ISLNK
from typing import Annotated, Any, Self, get_type_hints, override

from pydantic import BaseModel, Field

from ..config import normalize_unicode
from ..entries import (
    description_path_for_stem,
    is_description_file,
    is_inside_assets_dir,
    stem_path_from_reference,
)
from ..text import NOT_TEXT_REASON, DecodedText, read_text_file
from .scope import Scope

__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "WORKSPACE_PATH_HINT",
    "WORKSPACE_SCOPE_HINT",
    "AsyncPathTool",
    "AsyncTool",
    "BinaryAttachment",
    "CallInfo",
    "FullLinesArg",
    "IncludeIgnoredArg",
    "PathTool",
    "SearchPath",
    "SearchPathFilterFunc",
    "SyncTool",
    "Tool",
    "ToolOutput",
    "ToolRetry",
    "canonical_local_path",
    "coerce_paths",
    "entry_stat",
    "entry_visible",
    "excluded_dirs",
    "factory_tool_name",
    "file_allowed",
    "is_in_excluded_dir",
    "near_miss_hint",
    "read_text_or_retry",
    "resolve_accessible_file",
    "resolve_file_or_retry",
    "resolve_search_path",
    "resolve_tool_cls",
    "scope_paths",
    "sidecar_hint",
    "tool_description",
    "tool_name",
    "translate_tool_retry",
    "workspace_root_hint",
]


class ToolRetry(Exception):
    """A model-correctable tool failure.

    Raised by tools for inputs the model can fix and retry — a missing or
    inaccessible path, an ambiguous edit, or a stale ``expected_hash``. The
    framework adapters translate it: the pydantic-ai adapter re-raises it as
    ``ModelRetry`` so the model self-corrects, and the MCP adapter raises a
    FastMCP ``ToolError``. Keeping it framework-neutral lets the tool
    implementations stay free of any framework import.
    """


@contextmanager
def translate_tool_retry(into: Callable[[str], Exception]) -> Iterator[None]:
    """Re-raise any :class:`ToolRetry` from the block as *into* the message.

    The single translation point each framework adapter wraps its tool call
    in, so the retry signal reaches the framework in its own idiom (FastMCP
    ``ToolError``, pydantic-ai ``ModelRetry``) instead of aborting the run.
    """
    try:
        yield
    except ToolRetry as exc:
        raise into(str(exc)) from exc


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

FullLinesArg = Annotated[
    bool,
    Field(
        description=(
            "When true, return every line in full instead of clipping it to a "
            "per-line character budget. Use it for content whose tail carries "
            "meaning, such as a wide markdown table whose trailing columns "
            "would otherwise be cut off. The whole-output budget still "
            "applies, so expect fewer lines per call, not more text."
        ),
    ),
]

SearchPathFilterFunc = Callable[[str], bool] | None
"""Optional predicate that decides whether a filename is accessible."""

WORKSPACE_PATH_HINT = (
    "Give the document's full path exactly as the listing, search, grep, and "
    "read tools return it, including any leading scope prefix. A bare name with "
    "its scope prefix stripped may not identify the document."
)
"""Shared guidance for arguments that name a single document.

Reused across the file-path tool arguments so the model is told, in one
consistent voice, to copy paths verbatim. The concrete scope-prefix grammar is
the application's concern and lives in the system prompt, not here.
"""

WORKSPACE_SCOPE_HINT = (
    "Lead with a scope prefix (exactly as shown in tool results) to restrict "
    "the operation to that one scope; without a prefix it spans every scope you "
    "can access."
)
"""Shared guidance for filter arguments (a subdirectory or glob) that may lead
with a scope prefix to target a single search root."""


@dataclass(slots=True, frozen=True)
class SearchPath:
    """A labeled directory path with optional filter.

    Groups multiple search roots under a common interface. Each path may carry
    a :class:`~hivegent.tools.scope.Scope` so that results from different roots
    stay distinguishable and incoming paths can be matched back to their
    workspace, plus an optional *filter_func* that controls which files are
    visible.

    Attributes:
        path: Filesystem path to the directory to search.
        scope: Addressing scope whose prefix tags filenames from this path.
            ``None`` means the path carries no prefix (a single bare root).
        filter_func: Optional predicate controlling file visibility.
    """

    path: Path
    scope: Scope | None = None
    filter_func: SearchPathFilterFunc = None

    def prefixed(self, filename: str) -> str:
        """Return *filename* rendered under this path's scope."""
        return self.scope.render(filename) if self.scope is not None else filename


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


def _match_scope(
    paths: tuple[SearchPath, ...],
    raw: str,
) -> tuple[SearchPath, str] | None:
    """Return the first scoped path that claims *raw*, plus its local remainder.

    The shared scope-prefix scan behind :func:`resolve_search_path` and
    :func:`scope_paths`. A non-``None`` local is the prefix-stripped remainder
    (empty for a bare scope root); ``None`` means no scoped path matched.
    """
    for sp in paths:
        if sp.scope is not None:
            local = sp.scope.strip_prefix(raw)
            if local is not None:
                return sp, local
    return None


def resolve_search_path(
    paths: tuple[SearchPath, ...],
    filename: str,
) -> tuple[SearchPath, str] | None:
    """Find the :class:`SearchPath` matching a possibly-prefixed filename.

    A scope prefix is matched against each path's :attr:`SearchPath.scope`.
    An unprefixed filename falls back to the first scopeless path.

    The filename is folded to NFC here, the single funnel every path-taking
    tool passes through, so a decomposed spelling still resolves to the
    canonically named file on a normalization-sensitive filesystem.

    Args:
        paths: Ordered search paths to check.
        filename: A filename that may carry a scope prefix.

    Returns:
        ``(search_path, local_filename)`` with the prefix stripped,
        or ``None`` if no path matches.
    """
    filename = normalize_unicode(filename)
    match = _match_scope(paths, filename)
    if match is not None and match[1]:
        return match
    return next(((sp, filename) for sp in paths if sp.scope is None), None)


def scope_paths(
    paths: tuple[SearchPath, ...],
    raw: str | None,
) -> tuple[tuple[SearchPath, ...], str | None]:
    """Narrow *paths* to the workspace named by *raw*'s scope prefix.

    Filter arguments — a listing subdirectory or a grep glob — may lead
    with a workspace scope prefix to target a single workspace, mirroring
    the file-path tools.  When *raw* starts with a known prefix, only the
    matching :class:`SearchPath` is returned, together with the
    prefix-stripped remainder.  Otherwise *paths* and *raw* pass through
    unchanged, so an unprefixed value still spans every workspace and a
    missing value covers them all.

    Like :func:`resolve_search_path`, *raw* is folded to NFC so a decomposed
    subdirectory or glob still matches the canonically named entries.

    Args:
        paths: The search paths in scope for the call.
        raw: A possibly prefixed subdirectory or glob, or ``None``.

    Returns:
        ``(scoped_paths, local)`` where *local* has the matched prefix
        stripped, and is ``None`` when the prefix named a bare workspace
        root (e.g. ``~`` or ``@team``).
    """
    if not raw:
        return paths, raw
    raw = normalize_unicode(raw)
    match = _match_scope(paths, raw)
    if match is not None:
        sp, local = match
        return (sp,), local or None
    return paths, raw


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


def entry_visible(sp: SearchPath, rel_path: str, exclude_dirs: tuple[str, ...]) -> bool:
    """Whether the entry at *rel_path* may be shown for search path *sp*.

    The one definition of what a path tool hides by default, shared by the
    listing walk and the grep post-filter so ``include_ignored`` means the same
    thing in both.  Elements of ``.assets`` payload directories are hidden with
    the build and vendor directories: a single converted document can carry
    hundreds of extracted images, and only the directory itself is worth
    listing.  The search path's own filter always applies.
    """
    if is_in_excluded_dir(rel_path, exclude_dirs):
        return False
    if exclude_dirs and is_inside_assets_dir(rel_path):
        return False
    return file_allowed(sp.filter_func, rel_path)


def entry_stat(path: Path) -> stat_result | None:
    """Return *path*'s own stat, or ``None`` when it names no addressable entry.

    One ``lstat`` answers what ``is_symlink`` / ``is_dir`` / ``is_file`` ask in
    three, and a symlink is reported as absent rather than followed: its name
    is an alias for a file the document filter was never asked about, which is
    also why the upload pipeline refuses one.
    """
    try:
        st = path.lstat()
    except OSError:
        return None

    return None if S_ISLNK(st.st_mode) else st


def canonical_local_path(root: Path, local: str) -> tuple[str, Path] | None:
    """Resolve *local* under an already-resolved *root* to its canonical form.

    The one place a spelling becomes an identity: ``..`` segments are folded
    away and symlinks are followed, so the name a filter is asked about is the
    file the operation would actually touch, not the alias it was addressed
    by. Returns ``None`` when the target escapes *root* or is *root* itself,
    which names no entry.
    """
    absolute = (root / local).resolve()
    if not absolute.is_relative_to(root) or absolute == root:
        return None

    return absolute.relative_to(root).as_posix(), absolute


def resolve_accessible_file(
    paths: tuple[SearchPath, ...],
    file_path: str,
) -> tuple[SearchPath, str, Path] | None:
    """Resolve *file_path* to its search path, canonical name, and absolute path.

    Performs the checks every document tool shares: prefix resolution,
    traversal and symlink-escape defense, and the filter predicate. The
    filter is applied to the canonical path rather than the spelling the
    caller sent, so no alias — ``a/../b.md`` or a symlink — reaches a
    document the filter excludes. Does *not* require the file to exist —
    callers that need that should check :py:meth:`pathlib.Path.is_file` on
    the returned path.
    """
    resolved = resolve_search_path(paths, file_path)
    if resolved is None:
        return None
    sp, local = resolved
    canonical = canonical_local_path(sp.path.resolve(), local)
    if canonical is None:
        return None
    local, absolute = canonical
    if not file_allowed(sp.filter_func, local):
        return None

    return sp, local, absolute


def workspace_root_hint(paths: tuple[SearchPath, ...], file_path: str) -> str:
    """Name the addressable roots when *file_path* leads with none of them.

    Every refusal on a path argument ends here, so a caller that dropped the
    prefix is told which roots exist rather than left to guess another spelling
    — the one correction it cannot derive from context, since no root is
    implied.  Empty when the path does name a known root (the refusal then has
    another cause — a missing file, a filtered document, a traversal escape —
    that listing roots would only muddle) and empty for a single unscoped root,
    which has no prefix to give.
    """
    if resolve_search_path(paths, file_path) is not None:
        return ""

    roots = [root for sp in paths if (root := sp.prefixed(""))]
    if not roots:
        return ""

    return (
        f" This tool addresses {', '.join(roots)}; give the full path, "
        "leading with one of them."
    )


def near_miss_hint(absolute: Path) -> str:
    """Name the sibling that a missed path almost matched.

    A refusal on a path copied out of a listing one step earlier is otherwise
    unactionable: nothing in the message says which of the many ways a name can
    differ went wrong, and no respelling the caller can derive is more likely
    than the one it already sent.  Siblings are compared case-folded and
    NFC-folded, so a re-cased basename, the failure a canonical spelling
    cannot rule out since the filesystem is case-sensitive, is named
    outright.  Empty when nothing in the directory is equivalent.
    """
    target = normalize_unicode(absolute.name).casefold()
    try:
        siblings = sorted(
            name
            for path in absolute.parent.iterdir()
            if (name := path.name) != absolute.name
            and normalize_unicode(name).casefold() == target
        )
    except OSError:
        return ""

    if not siblings:
        return ""

    return f" A file with an equivalent name exists: {', '.join(siblings)}."


def resolve_file_or_retry(
    paths: tuple[SearchPath, ...], file_path: str
) -> tuple[SearchPath, str, Path]:
    """Resolve *file_path* to an existing file, or raise a correctable refusal.

    Every reader owes a turned-away caller the same correction, and which one
    it owes splits on the reason: a path naming no root is told which roots
    exist, one naming a missing file is told which sibling it almost matched.
    Neither is derivable from context, so the two hints live together here
    rather than being re-decided per tool.

    The mutating tools resolve through their own gateway instead: a mutation
    may legitimately create the file, so absence is not a refusal there.
    """
    resolved = resolve_accessible_file(paths, file_path)
    if resolved is None or not resolved[2].is_file():
        hint = (
            workspace_root_hint(paths, file_path)
            if resolved is None
            else near_miss_hint(resolved[2])
        )
        raise ToolRetry(f"'{file_path}' not found.{hint}")

    return resolved


def sidecar_hint(file_path: str) -> str:
    """Nudge a refused read toward the entry's extracted text, when it has one.

    Every refusal on either reader ends here, so a caller turned away by one
    tool is never sent to the other one to be turned away again: whatever the
    format, a converted entry's text is reachable at its ``<stem>.md``.  Empty
    for a description, which is that text.
    """
    if is_description_file(file_path):
        return ""
    sidecar = description_path_for_stem(stem_path_from_reference(file_path))

    return f" To read its extracted text, request '{sidecar}' instead."


def read_text_or_retry(path: Path, file_path: str, hint: str = "") -> DecodedText:
    """Decode a resolved file, or raise :class:`ToolRetry` when it is not text.

    Legacy encodings are decoded rather than refused, so callers can report the
    source encoding instead of failing on it.
    """
    decoded = read_text_file(path)
    if decoded is None:
        raise ToolRetry(f"'{file_path}' {NOT_TEXT_REASON}.{hint}")

    return decoded


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
    :class:`AsyncPathTool`.
    """

    paths: Path | SearchPath | tuple[SearchPath, ...] = ()

    @property
    def resolved_paths(self) -> tuple[SearchPath, ...]:
        """Return *paths* normalised to a ``SearchPath`` tuple."""
        return coerce_paths(self.paths)

    def scoped(self, raw: str | None) -> tuple[tuple[SearchPath, ...], str | None]:
        """Narrow :attr:`resolved_paths` to the workspace named by *raw*'s prefix.

        Thin wrapper over :func:`scope_paths` for the filter tools.
        """
        return scope_paths(self.resolved_paths, raw)


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

    def without(self, *annotations: Any) -> Self:
        """Drop the parameters carrying any of these annotations.

        Addressed by annotation rather than by name: the shared ``Annotated``
        alias a tool declares an argument with is what says the argument is
        that one, so a surface names the type it cannot honour instead of
        keeping a string of the spelling in step with it.

        Both adapters synthesize a signature rather than edit one — the
        FastMCP one already appends a ``_tool_`` parameter no ``__call__``
        declares — so leaving an argument out is the same act as putting one
        in, and no schema is rewritten after the fact.  It is what a surface
        uses for an argument it could only advertise and then refuse on every
        call, which is a defect in the schema rather than a mode.
        """
        dropped = {
            name for name, hint in self.annotations.items() if hint in annotations
        }

        return replace(
            self,
            params=tuple(p for p in self.params if p.name not in dropped),
            annotations={
                name: hint
                for name, hint in self.annotations.items()
                if name not in dropped
            },
        )

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
