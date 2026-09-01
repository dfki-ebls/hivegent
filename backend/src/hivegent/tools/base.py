"""Shared base protocol and helpers for tool implementations."""

import inspect
import json
import re
import types
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import cache, cached_property, reduce
from operator import or_
from os import stat_result
from pathlib import Path
from stat import S_ISLNK
from typing import (
    Annotated,
    Any,
    ClassVar,
    NoDefault,
    Self,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    override,
)

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, create_model
from pydantic.json_schema import JsonSchemaValue

from ..config import normalize_unicode
from ..converters import is_json, is_tabular
from ..entries import (
    description_path_for_stem,
    find_original_for_stem,
    is_description_file,
    is_inside_assets_dir,
    stem_path_from_reference,
)
from ..text import MAX_BYTES_PER_CHAR, NOT_TEXT_REASON, DecodedText, read_text_file
from .scope import Scope

__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "WORKSPACE_SCOPE_HINT",
    "AsyncPathTool",
    "AsyncTool",
    "BinaryAttachment",
    "FullLinesArg",
    "IncludeIgnoredArg",
    "PathTool",
    "SearchPath",
    "SearchPathFilterFunc",
    "SyncTool",
    "Tool",
    "ToolOutput",
    "ToolRetry",
    "ToolSpec",
    "Unreachable",
    "addressable_roots",
    "canonical_local_path",
    "check_read_budget",
    "coerce_paths",
    "entry_stat",
    "entry_visible",
    "excluded_dirs",
    "factory_tool_name",
    "file_allowed",
    "is_in_excluded_dir",
    "match_scope",
    "near_miss_hint",
    "query_hint",
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
            "When true, include the build and vendor directories "
            "(node_modules, .git, dist, ...) skipped by default."
        ),
    ),
]

FullLinesArg = Annotated[
    bool,
    Field(
        description=(
            "When true, return every line in full instead of clipping it to a "
            "per-line budget, for content whose tail carries meaning such as a "
            "wide table. The whole-output budget still applies, so expect "
            "fewer lines, not more text."
        ),
    ),
]

SearchPathFilterFunc = Callable[[str], bool] | None
"""Optional predicate that decides whether a filename is accessible."""

WORKSPACE_SCOPE_HINT = (
    "Lead with a scope prefix to restrict the operation to that one scope; "
    "without a prefix it spans every scope you can access."
)
"""Shared guidance for filter arguments (a subdirectory or glob) that may lead
with a scope prefix to target a single search root.

Its counterpart -- copy a document's full path verbatim, prefix included -- is
stated once per surface instead of once per argument: ``WORKSPACE_PATH_INSTRUCTIONS``
carries it for an agent run, the FastMCP app instructions for the MCP one.  Eight
arguments repeating it cost more on every request than the one sentence saves.
"""


# Not frozen, alone among the value types here, because it normalises its own
# input: a root has to be resolved before anything can be contained in it, and
# a frozen dataclass can only assign in `__post_init__` by reaching around its
# own immutability with `object.__setattr__`.  Nothing mutates an instance
# after construction, and nothing hashes one, so the guarantee that is given up
# was never being used.
@dataclass(slots=True)
class SearchPath:
    """A labeled directory path with optional filter.

    Groups multiple search roots under a common interface. Each path may carry
    a :class:`~hivegent.tools.scope.Scope` so that results from different roots
    stay distinguishable and incoming paths can be matched back to their
    workspace, plus an optional *filter_func* that controls which files are
    visible.

    Attributes:
        path: Directory to search, folded to its real location on construction.
        scope: Addressing scope whose prefix tags filenames from this path.
            ``None`` means the path carries no prefix (a single bare root).
        filter_func: Optional predicate controlling file visibility.
    """

    path: Path
    scope: Scope | None = None
    filter_func: SearchPathFilterFunc = None

    def __post_init__(self) -> None:
        """Fold the root once, here, rather than once per path resolved under it.

        Every containment check needs the root's real location, so the
        resolution is not optional, only its frequency is, and a root does not
        move for the length of a call.  It belongs on the type rather than on
        the callers because several of them hand a root straight to
        :func:`resolve_accessible_file` without passing through
        :func:`coerce_paths`, and a root that missed the fold would not be
        wrong in a corner: every document under it would simply cease to exist.
        A root that has not been created yet folds to itself, which is what a
        workspace nobody has written to needs, since handing a tool its roots
        must not create one.
        """
        self.path = self.path.resolve()

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


def match_scope(
    paths: tuple[SearchPath, ...],
    raw: str,
) -> tuple[SearchPath, str] | None:
    """Return the first scoped path that claims *raw*, plus its local remainder.

    The shared scope-prefix scan behind :func:`resolve_search_path` and
    :func:`scope_paths`. A non-``None`` local is the prefix-stripped remainder
    (empty for a bare scope root); ``None`` means no scoped path matched.

    Public for the one caller that wants the case the two resolvers drop: a
    bare scope root is a directory the sandbox mount lists, where it is a path
    argument no document tool takes.
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
    match = match_scope(paths, filename)
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
    match = match_scope(paths, raw)
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


def check_read_budget(canonical: str, size: int, limit: int) -> None:
    """Refuse a file too large to decode into a caller's character budget.

    Sized before it is decoded, so a file that cannot fit whatever it decodes
    to is refused without ever being read into memory: anything above
    :data:`~hivegent.text.MAX_BYTES_PER_CHAR` times the limit is over it for
    certain, and anything smaller is left to the exact check the caller runs on
    the decoded text.

    The bound is per file rather than per run, because one decoded file is what
    a reader actually holds: :func:`read_text_file` reads the whole thing and
    decodes it in one go, so an unbounded file is one unbounded allocation in
    the server process, while reading a thousand bounded ones in turn is not.

    Shared because a ``run_python`` call applies the same rule from three
    places, the script it loads before the sandbox starts, the output document
    it fingerprints, and every document the mounted workspace hands the
    program, and three copies of it would be three budgets wearing one name.
    """
    if size <= limit * MAX_BYTES_PER_CHAR:
        return

    raise ToolRetry(
        f"'{canonical}' is too large to read here ({size} bytes, and one "
        f"document may hold at most {limit} characters)."
    )


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
    canonical = canonical_local_path(sp.path, local)
    if canonical is None:
        return None
    local, absolute = canonical
    if not file_allowed(sp.filter_func, local):
        return None

    return sp, local, absolute


def addressable_roots(paths: tuple[SearchPath, ...]) -> list[str]:
    """The prefixes a path may lead with, which is what a refusal has to name.

    Empty for an unscoped root, which has no prefix to give and therefore
    nothing to offer a caller that led with the wrong one.  One definition, so
    the sandbox's own refusal names the same roots this one does.
    """
    return [root for sp in paths if (root := sp.prefixed(""))]


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

    roots = addressable_roots(paths)
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


def _query_phrase(file_path: str) -> str:
    """How a query tool answers *file_path* in place, without reading it.

    The wording both hints share, so the tool a caller is pointed at is named
    the same whether it addressed the original and was refused or addressed
    the description and was served.  Empty for a format no query tool claims.
    The identifiers lead the sentence uncapitalised, since they are the names
    the caller has to type.
    """
    if is_tabular(file_path):
        return "query_table runs SQL over it and returns only the rows you ask for"

    if is_json(file_path):
        return (
            "jq filters it and returns only the values you select; call it "
            "without a filter for its shape"
        )

    return ""


def sidecar_hint(file_path: str) -> str:
    """Nudge a refused read toward the entry's extracted text, when it has one.

    Every refusal on either reader ends here, so a caller turned away by one
    tool is never sent to the other one to be turned away again: whatever the
    format, a converted entry's text is reachable at its ``<stem>.md``.  Empty
    for a description, which is that text.

    A table or a JSON document is refused here too, and its extracted text is
    the worst of the three answers, so the tool that queries the original
    without reading it leads and the sidecar follows.
    """
    if is_description_file(file_path):
        return ""
    sidecar = description_path_for_stem(stem_path_from_reference(file_path))
    query = _query_phrase(file_path)
    lead = f" {query}." if query else ""

    return f"{lead} To read its extracted text, request '{sidecar}' instead."


def query_hint(sp: SearchPath, local: str) -> str:
    """Name the tool that queries the entry *local* belongs to, if one does.

    Keyed on the entry rather than on the path the caller addressed: an
    uploaded table is served as its ``<stem>.md`` projection, so the path a
    read or a selection has in hand is never the one ``query_table`` takes,
    and asking the suffix alone left the tool invisible for exactly the file
    it exists for.  A table dropped in as text (a bare ``.csv``) is its own
    original and answers the same question directly.

    One sentence for both surfaces that need it, the read that was served the
    projection and the document scope that named it, so the run is told the
    same thing whichever of the two reaches it first.  Both resolve a path
    before they get here for their own reasons, so this takes what they hold
    rather than resolving it a second time.

    Empty when the entry has no original, when no query tool claims its
    format, or when the filter hides it — the same predicate the reader
    answers to, so a hint can never name a document its reader would refuse.
    """
    original = (
        find_original_for_stem(sp.path, stem_path_from_reference(local))
        if is_description_file(local)
        else local
    )
    if original is None or not file_allowed(sp.filter_func, original):
        return ""

    phrase = _query_phrase(original)

    return f"{phrase}, on '{sp.prefixed(original)}'" if phrase else ""


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

    injectable: ClassVar[bool] = False
    """Whether a sandboxed program may be handed this tool as a function.

    True for the few whose answer no program can work out for itself: retrieval
    reaches the database, the web tools the network, a spreadsheet a decoder
    the interpreter does not have.  False for everything the workspace mount
    already provides — ``open`` is the read tools, ``re`` is grep, ``json`` is
    jq — and for everything that mutates, since a running program cannot stop
    to ask for approval.

    Declared here rather than listed elsewhere because it is a property of the
    tool: whichever surface registers it, the same answer holds.  Only
    :mod:`hivegent.agents.tools.compute` reads it, which is what keeps the
    injectable set derived from the registered one instead of kept beside it.
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


def _typevar_bindings(annotation: Any) -> dict[TypeVar, Any]:
    """Resolve a tool annotation's type variables through its generic bases."""
    bindings: dict[TypeVar, Any] = {}
    pending = [annotation]

    while pending:
        current = pending.pop()
        origin = get_origin(current) or current
        parameters = getattr(origin, "__type_params__", ())
        arguments = get_args(current)

        if parameters and not arguments:
            arguments = tuple(parameter.__default__ for parameter in parameters)

        if parameters and NoDefault not in arguments:
            bindings.update(
                zip(
                    parameters,
                    (_replace_typevars(argument, bindings) for argument in arguments),
                    strict=True,
                )
            )

        for base in getattr(origin, "__orig_bases__", ()):
            base_origin = get_origin(base) or base

            if isinstance(base_origin, type) and issubclass(base_origin, Tool):
                pending.append(_replace_typevars(base, bindings))

    return bindings


def _replace_typevars(annotation: Any, bindings: Mapping[TypeVar, Any]) -> Any:
    """Apply known type variable bindings to a generic annotation."""
    if isinstance(annotation, TypeVar):
        return bindings.get(annotation, annotation)

    metadata = getattr(annotation, "__pydantic_generic_metadata__", None)
    if metadata is not None and metadata["parameters"]:
        origin = metadata["origin"] or annotation
        replaced = tuple(
            _replace_typevars(argument, bindings) for argument in metadata["args"]
        )

        return origin.__class_getitem__(replaced)

    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is None or not arguments:
        return annotation

    replaced = tuple(_replace_typevars(argument, bindings) for argument in arguments)

    if origin is types.UnionType:
        return reduce(or_, replaced)

    return origin[replaced]


def _has_typevar(annotation: Any) -> bool:
    """Whether an annotation still contains an unresolved type parameter."""
    if isinstance(annotation, TypeVar):
        return True

    metadata = getattr(annotation, "__pydantic_generic_metadata__", None)
    if metadata is not None and metadata["parameters"]:
        return True

    return any(_has_typevar(argument) for argument in get_args(annotation))


def resolve_tool_cls(factory: Callable[..., Tool[Any]]) -> type[Tool[Any]]:
    """Extract the Tool subclass from a factory's return type annotation.

    Handles parameterized generics like ``VectorSearchTool[str]`` by
    unwrapping them to their origin.

    Args:
        factory: A callable whose return annotation is a ``Tool`` subclass.

    Returns:
        The Tool subclass.

    Raises:
        TypeError: If the return annotation is missing or not a Tool subclass.
    """
    annotation = get_type_hints(factory).get("return")
    tool_cls = get_origin(annotation) or annotation
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
class Unreachable:
    """Marks the result branch an argument is the only way to reach.

    Carried on the argument's own ``Annotated`` alias, so a surface dropping
    the argument through :meth:`ToolSpec.without` drops the branch with it
    rather than being asked to name both.
    """

    data_type: Any


# `slots=True` is deliberately absent, and the derived members below are the
# reason: `cached_property` stores into the instance `__dict__` a slotted class
# does not have.  They have to be memoised somehow, since building one costs
# ~250us and `argument_model` and `data_adapter` are read on every sandbox and
# MCP call, and they have to be derived rather than stored, or `replace` in
# `without` would carry a schema onto the narrowed spec it does not describe.
# The slotted alternative is a `dict[str, Any]` memo field, which launders
# every value through `Any` behind string keys no checker reads; slots buys
# nothing against that here, since `from_factory` caches one spec per tool for
# the life of the process.
@dataclass(frozen=True)
class ToolSpec:
    """Framework-neutral contract for a tool's arguments and structured data.

    Provides what the three surfaces built from a tool -- pydantic-ai, MCP, and
    the sandbox -- need to build wrapper functions with correct signatures and
    type annotations.

    The six fields are what a tool declares.  Everything below them is a pure
    function of those, derived once on first use rather than stored beside
    them: a schema and the validator that has to agree with it are then built
    one way for every surface, and a surface pays only for what it reads, which
    for pydantic-ai is none of it.

    Attributes:
        name: Tool name derived from the factory function.
        description: Canonical tool description from the Tool class.
        params: ``__call__`` parameters with ``self`` removed, each carrying
            the resolved annotation.
        data_type: Concrete type carried by :attr:`ToolOutput.data`.
        is_async: Whether ``__call__`` is a coroutine function.
        source_module: Module name of the originating factory.
    """

    name: str
    description: str | None
    params: tuple[inspect.Parameter, ...]
    data_type: Any
    is_async: bool
    source_module: str

    @cached_property
    def annotations(self) -> Mapping[str, Any]:
        """Resolved type hints for ``__call__`` parameters, keyed by name.

        Read off :attr:`params`, which carry the resolved annotation, rather
        than stored beside them: the two would otherwise be the same data in
        two places, and a surface that drops a parameter would have to drop it
        twice.
        """
        return types.MappingProxyType({p.name: p.annotation for p in self.params})

    @cached_property
    def argument_model(self) -> type[BaseModel]:
        """Validator for one call's arguments, built from :attr:`params`."""
        fields: dict[str, Any] = {
            param.name: (
                param.annotation,
                ... if param.default is param.empty else param.default,
            )
            for param in self.params
        }

        return create_model(
            f"{self.name}_arguments",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

    @cached_property
    def parameters_json_schema(self) -> JsonSchemaValue:
        """JSON schema of the arguments :attr:`argument_model` validates."""
        return self.argument_model.model_json_schema()

    @cached_property
    def data_adapter(self) -> TypeAdapter[Any]:
        """Serialiser for :attr:`data_type`."""
        return TypeAdapter(self.data_type)

    @cached_property
    def data_json_schema(self) -> JsonSchemaValue:
        """Serialization-mode JSON schema of :attr:`data_type`."""
        return self.data_adapter.json_schema(mode="serialization")

    def validate_arguments(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, convert, and default arguments before invoking a tool."""
        return dict(self.argument_model.model_validate(arguments).__dict__)

    def serialize_data(self, data: Any) -> JsonValue:
        """Serialize a structured tool payload to plain JSON-compatible values."""
        return cast(JsonValue, self.data_adapter.dump_python(data, mode="json"))

    def without(self, *annotations: Any) -> Self:
        """Drop the parameters carrying any of these annotations.

        Addressed by annotation rather than by name: the shared ``Annotated``
        alias a tool declares an argument with is what says the argument is
        that one, so a surface names the type it cannot honour instead of
        keeping a string of the spelling in step with it.

        All three surfaces synthesize a signature rather than edit one, the
        FastMCP one already appending a ``_tool_`` parameter no ``__call__``
        declares, so leaving an argument out is the same act as putting one
        in, and no schema is rewritten after the fact.  It is what a surface
        uses for an argument it could only advertise and then refuse on every
        call, which is a defect in the schema rather than a mode.

        A result branch reachable only through a dropped argument goes with it,
        read off that argument's own :class:`Unreachable` metadata rather than
        named a second time by the caller.  A surface that hands out no writer
        has said so once and both halves follow from the one fact, where naming
        them separately let the MCP surface drop the argument and go on
        advertising the receipt it could no longer return.
        """
        dropped = {
            name for name, hint in self.annotations.items() if hint in annotations
        }
        if not dropped:
            return self

        unreachable = {
            meta.data_type
            for hint in annotations
            for meta in get_args(hint)
            if isinstance(meta, Unreachable)
        }
        members = (
            get_args(self.data_type)
            if get_origin(self.data_type) is types.UnionType
            else ()
        )
        remaining = tuple(member for member in members if member not in unreachable)
        if members and not remaining:
            msg = f"{self.name!r} has no result type once {sorted(dropped)} is dropped"
            raise TypeError(msg)

        return replace(
            self,
            params=tuple(p for p in self.params if p.name not in dropped),
            data_type=reduce(or_, remaining) if remaining else self.data_type,
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
    @cache
    def from_factory(cls, factory: Callable[..., Tool[Any]]) -> Self:
        """Extract call metadata from a Tool factory's return type.

        Resolves the Tool subclass from *factory*'s return annotation,
        then inspects its ``__call__`` method to build a :class:`ToolSpec`.

        Cached on the factory, which is what makes this affordable on the
        sandbox path: the two schema-building adapters run it once at import,
        but the sandbox rebuilds its surface on every model request, and
        resolving type hints is the whole cost.  Safe to share because the
        result is frozen through to its fields -- ``params`` is a tuple of
        immutable parameters and ``annotations`` a mapping -- and every caller
        derives a new one through :meth:`without` rather than editing this.

        Args:
            factory: A callable whose return annotation is a ``Tool``
                subclass.

        Returns:
            Extracted tool contract.

        Raises:
            TypeError: If the factory's return annotation is not a Tool
                subclass, if any ``__call__`` parameter (besides ``self``)
                lacks a type annotation, or if ``__call__`` does not return
                a ``ToolOutput``.
        """
        tool_cls = resolve_tool_cls(factory)
        factory_annotation = get_type_hints(factory)["return"]
        bindings = _typevar_bindings(factory_annotation)
        call = tool_cls.__call__
        sig = inspect.signature(call)
        hints = get_type_hints(call, include_extras=True)
        cls_name = tool_cls.__qualname__

        params = tuple(p for name, p in sig.parameters.items() if name != "self")

        for p in params:
            if p.name not in hints:
                msg = f"{cls_name}.__call__ has unannotated parameter {p.name!r}"
                raise TypeError(msg)

        # `SyncTool`/`AsyncTool` already declare the envelope, so what this
        # catches is the annotation that never resolved to one -- which used to
        # leave `returns` as None and surface far away, in the sandbox, as a
        # missing generic argument.
        returns = hints.get("return")
        if not (isinstance(returns, type) and issubclass(returns, ToolOutput)):
            msg = f"{cls_name}.__call__ must return ToolOutput[...], got {returns!r}"
            raise TypeError(msg)

        annotations = {
            name: _replace_typevars(hint, bindings)
            for name, hint in hints.items()
            if name not in ("self", "return")
        }
        # A factory annotated `-> VectorSearchTool[RetrievedChunk]` names the
        # record its `list[R]` holds, and resolving the tool class discards it.
        # Binding it here keeps every field concrete for whoever reads it, and
        # this is the only place the parameterised annotation and the resolved
        # one are both in hand.
        envelope = cast(type[ToolOutput[Any]], _replace_typevars(returns, bindings))
        unresolved = [
            name for name, annotation in annotations.items() if _has_typevar(annotation)
        ]
        if envelope.__pydantic_generic_metadata__["parameters"]:
            unresolved.append("return")
        if unresolved:
            name = getattr(factory, "__qualname__", repr(factory))
            joined = ", ".join(unresolved)
            raise TypeError(f"{name!r} leaves tool parameters unresolved: {joined}")

        (data_type,) = envelope.__pydantic_generic_metadata__["args"]

        return cls(
            name=factory_tool_name(factory),
            description=tool_description(tool_cls),
            params=tuple(
                param.replace(annotation=annotations[param.name]) for param in params
            ),
            data_type=data_type,
            is_async=inspect.iscoroutinefunction(call),
            source_module=getattr(factory, "__module__", ""),
        )
