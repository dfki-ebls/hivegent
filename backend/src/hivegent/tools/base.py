"""Shared helpers of the tools: search paths, refusals, batches, and line clips.

Tools are plain typed async functions, built by factories that close over their configuration,
with the conventions of `exar.tools`: an `effect` declares what a call changes,
`exar.tools.Retry` refuses input the model can fix, and a list argument is served item by item.
A tool returns a pydantic-ai `ToolReturn` (`tool_return`), whose `return_value` is the text the model reads
and whose `metadata` is the typed data programs and the client receive.
The type parameter of `ToolReturn[T]` names the type of that data,
which pydantic-ai publishes as the return schema the sandbox declares to programs.
"""

import reprlib
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, ClassVar, Protocol, Self

from cbrkit.io import COMPANION_SUFFIXES
from exar.memory.mounts import (
    Mount,
    canonical,
    claim,
    resolve,
)
from exar.text import truncate_line
from exar.tools import common as exar_common
from exar.tools.common import Refusal, is_in_excluded_dir

from ..shared.formats import is_json, is_tabular
from .entries import (
    Listdir,
    Part,
    find_original_for_stem,
    is_description_file,
    is_reserved_path,
    leads_with,
    stem_path_from_reference,
)

__all__ = [
    "HIDDEN_ENTRIES",
    "WORKSPACE_SCOPE_HINT",
    "CallBudget",
    "CommitPolicy",
    "Direct",
    "ModeGated",
    "PathFilter",
    "Reviewed",
    "SearchPath",
    "ToolFunc",
    "entry_visible",
    "match_scope",
    "mounts_of",
    "omission_hints",
    "query_hint",
    "refusal_hint",
    "render_arguments",
    "resolve_accessible_file",
    "resolve_file_or_retry",
    "resolve_search_path",
    "sidecar_hint",
    "strictest",
    "workspace_root_hint",
]


@dataclass(slots=True)
class CallBudget:
    """An exact number of calls, shared by everything handed the same budget.

    Exact under ``asyncio.gather`` because :meth:`take` checks and counts with
    no await in between, which usage limits cannot be: their check and their
    increment sit on either side of the model request.

    >>> budget = CallBudget(1)
    >>> budget.take("spent")
    >>> budget.take("spent")
    Traceback (most recent call last):
    ...
    RuntimeError: spent
    """

    limit: int
    used: int = 0

    def take(self, refusal: str) -> None:
        """Count one call, or raise a ``RuntimeError`` of *refusal* when none is left."""
        if self.used >= self.limit:
            raise RuntimeError(refusal)

        self.used += 1


class PathFilter(Protocol):
    """Which paths below a root its tools may see, decided by a finite set of entries."""

    def __call__(self, path: str) -> bool:
        """Whether the file or directory *path* is visible."""
        ...

    def hides_within(self, path: str) -> bool:
        """Whether *path* or anything below it is hidden, answered without listing it."""
        ...

    def globs(self, root: Path) -> tuple[str, ...]:
        """The paths it hides below *root* as the ``exclude`` globs of a :class:`~exar.memory.mounts.Mount`."""
        ...


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


class _ReservesAssets:
    """A root reserving each ``.assets`` payload in it, since it belongs to its document."""

    __slots__ = ()

    def reserves(self, root: Path, local: str) -> bool:
        """Whether *local* is or lies in an ``.assets`` payload, wherever *root* is."""
        return is_reserved_path(local)


@dataclass(slots=True, frozen=True)
class ModeGated(_ReservesAssets):
    """A workspace root, whose changes are approved where the run's mode asks."""

    strictness: ClassVar[int] = 1


@dataclass(slots=True, frozen=True)
class Direct:
    """A folder root, whose changes are written with no approval.

    Attributes:
        reserved: A folder directly below the root that only the host
            changes, while every tool may read it.
    """

    reserved: str | None = None

    strictness: ClassVar[int] = 0
    """How much it asks of a change, the most of a change reaching several roots winning."""

    def reserves(self, root: Path, local: str) -> bool:
        """Whether *local* is or lies in :attr:`reserved`, compared as *root*'s filesystem does."""
        return leads_with(root, local, self.reserved)


@dataclass(slots=True, frozen=True)
class Reviewed(_ReservesAssets):
    """A root whose every change a person approves, in every mode.

    It reserves what a :class:`ModeGated` one does, so a workspace may be either.
    """

    strictness: ClassVar[int] = 2


type CommitPolicy = Direct | ModeGated | Reviewed
"""Whether a change to a root's files is approved, and what it reserves, the root's alone.

The host decides what each one means for a run (``agents/policy.py``).
"""

_GATED = ModeGated()


def strictest(policies: Iterable[CommitPolicy]) -> CommitPolicy:
    """The policy of *policies* that asks the most, as a change reaching several roots answers to.

    >>> strictest((ModeGated(), Reviewed()))
    Reviewed()
    """
    return max(policies, key=lambda policy: policy.strictness)


HIDDEN_GLOBS = (
    "**/*.assets/**",
    *(f"**/?*{suffix}" for suffix in COMPANION_SUFFIXES.values()),
)
"""The ``hidden`` globs of every workspace: the elements of ``.assets`` payloads, which a single converted document can fill with hundreds of extracted images,
and the cbrkit companions of a case base, since ``similar_cases`` shows its configuration."""


@dataclass(slots=True, frozen=True)
class SearchPath:
    """A workspace folder as the tools see it, its mount with the filter and policy the write gateway asks.

    Each mount leads with a *target* so that results from different roots stay
    distinguishable and incoming paths can be matched back to their workspace,
    and an optional *filter_func* controls which files are visible.  What the
    read tools see of it is its :attr:`mount`, while the write gateway asks the
    filter and the policy itself.

    Attributes:
        mount: The folder as the read tools address it, its source resolved.
        filter_func: Optional predicate controlling file visibility.
        policy: How a change to a file under the root commits.
    """

    mount: Mount
    filter_func: PathFilter | None = None
    policy: CommitPolicy = _GATED

    @classmethod
    def of(
        cls,
        path: Path,
        target: str = "",
        *,
        implied: bool = False,
        filter_func: PathFilter | None = None,
        policy: CommitPolicy = _GATED,
    ) -> Self:
        """The search path of a folder, resolved once for every containment check, a missing one to itself.

        Args:
            path: The folder to search.
            target: The mount point whose prefix tags its files, empty for a single bare root.
            implied: Whether a path leading with no target is shorthand for one in it.
            filter_func: Optional predicate controlling file visibility.
            policy: How a change to a file under it commits.
        """
        path = path.resolve()
        mount = Mount(
            source=path,
            target=target,
            implied=implied,
            exclude=() if filter_func is None else filter_func.globs(path),
            hidden=HIDDEN_GLOBS,
        )

        return cls(mount, filter_func, policy)

    @property
    def path(self) -> Path:
        """The folder, resolved."""
        return self.mount.source

    def is_reserved(self, local: str) -> bool:
        """Whether *local* is or lies in a subtree its root keeps from ordinary changes.

        >>> SearchPath.of(Path("/w")).is_reserved("notes/report.assets/fig1.png")
        True
        """
        return self.policy.reserves(self.path, local)


def mounts_of(paths: Sequence[SearchPath]) -> tuple[Mount, ...]:
    """The mounts of the search paths, which the read tools of ``exar.tools.files`` take."""
    return tuple(sp.mount for sp in paths)


def _path_of(paths: Sequence[SearchPath], mount: Mount) -> SearchPath:
    """The search path whose mount it is."""
    return next(sp for sp in paths if sp.mount is mount)


def match_scope(paths: Sequence[SearchPath], raw: str) -> tuple[SearchPath, str] | None:
    """The search path that claims *raw* by its prefix and the local remainder, empty for a bare prefix."""
    claimed = claim(mounts_of(paths), raw)

    return None if claimed is None else (_path_of(paths, claimed[0]), claimed[1])


def resolve_search_path(
    paths: Sequence[SearchPath], raw: str
) -> tuple[SearchPath, str] | None:
    """The search path *raw* names and its local path, an unprefixed one falling back to the personal workspace.

    Raises:
        Retry: If *raw* holds a NUL byte, which no file name can.
    """
    exar_common.refuse_nul(raw)
    resolved = resolve(mounts_of(paths), raw)

    return None if resolved is None else (_path_of(paths, resolved[0]), resolved[1])


def entry_visible(sp: SearchPath, rel_path: str, exclude_dirs: tuple[str, ...]) -> bool:
    """Whether the entry at *rel_path* may be shown for search path *sp*.

    The one definition of what a path tool hides by default, shared by the
    listing walk and ``exar.tools.files`` so ``include_ignored`` means the same
    thing in both: not in an excluded directory, and not hidden while
    *exclude_dirs* are in force, and never one the filter of the path excludes.
    """
    return not (
        is_in_excluded_dir(rel_path, exclude_dirs)
        or (bool(exclude_dirs) and sp.mount.hides(rel_path))
        or sp.mount.excludes(rel_path)
    )


def resolve_accessible_file(
    paths: tuple[SearchPath, ...],
    file_path: str,
    listdir: Listdir | None = None,
) -> tuple[SearchPath, str, Path] | None:
    """Resolve *file_path* to its search path, canonical name, and absolute path.

    Performs the checks every document tool shares: prefix resolution,
    traversal and symlink-escape defense, and the filter predicate. The
    filter is applied to the canonical path rather than the spelling the
    caller sent, so no alias (``a/../b.md`` or a symlink) reaches a
    document the filter excludes. Does *not* require the file to exist.
    Callers that need that should check :py:meth:`pathlib.Path.is_file` on
    the returned path.
    """
    resolved = resolve_search_path(paths, file_path)
    if resolved is None:
        return None
    sp, local = resolved
    found = canonical(sp.path, local, listdir)

    if found is None or sp.mount.excludes(found[0]):
        return None

    return sp, *found


def refusal_hint(refusal: Refusal, path: str) -> str:
    """What the tools of the workspaces add to a refusal of *path*: the tool to use instead."""
    match refusal:
        case "directory":
            return " Use list_documents to see the documents in it."
        case "format":
            return f" Read anything else with read_document.{sidecar_hint(path)}"
        case "binary":
            return sidecar_hint(path)
        case "media":
            return f" Use read_document if it is text.{sidecar_hint(path)}"


HIDDEN_ENTRIES = "`.assets` contents and common build/vendor directories"
"""What ``include_ignored`` reveals in the workspaces, as the hint on hidden entries names it."""

omission_hints = partial(exar_common.omission_hints, what=HIDDEN_ENTRIES)


def workspace_root_hint(paths: Sequence[SearchPath], file_path: str) -> str:
    """Name the targets of the *paths* when *file_path* leads with none of them, else nothing."""
    return exar_common.workspace_root_hint(mounts_of(paths), file_path)


def resolve_file_or_retry(
    paths: tuple[SearchPath, ...], file_path: str
) -> tuple[SearchPath, str, Path]:
    """Resolve *file_path* to an existing file, or raise a correctable refusal.

    A path naming no root is told which roots exist, a missing file which
    sibling it almost matched, and a directory to be listed instead.
    """
    mount, local, _absolute = exar_common.resolve_file_or_retry(
        mounts_of(paths), file_path, refusal_hint
    )
    sp = _path_of(paths, mount)

    return sp, local, sp.path / local


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
    sidecar = Part(stem_path_from_reference(file_path), "description").path
    query = _query_phrase(file_path)
    lead = f" {query}." if query else ""

    return f"{lead} To read its extracted text, request '{sidecar}' instead."


def query_hint(mount: Mount, local: str) -> str:
    """Name the tool that queries the original of the entry *local* belongs to, if one does.

    Keyed on the entry, since a table is served as its ``<stem>.md``
    projection.  Empty when the entry has no original, no query tool claims
    its format, or the filter hides it.
    """
    original = (
        find_original_for_stem(mount.root(), stem_path_from_reference(local))
        if is_description_file(local)
        else local
    )
    if original is None or mount.excludes(original):
        return ""

    phrase = _query_phrase(original)

    return f"{phrase}, on '{mount.render(original)}'" if phrase else ""


type ToolFunc = Callable[..., Awaitable[Any]]
"""A tool, a plain typed async function named and described by itself."""

_ARGUMENT_REPR = reprlib.Repr(maxlevel=2, maxstring=200)
"""Renders one argument value cut short, never the whole of a large one."""


def render_arguments(args: str | Mapping[str, object] | None, max_chars: int) -> str:
    """The keyword arguments of a call as it spells them, within *max_chars*.

    A large argument, such as a written document or a program, is cut while it
    is rendered rather than serialized whole and cut afterwards.  A string is
    the arguments a model sent already serialized.

    >>> render_arguments({"query": "x", "limit": 5}, 200)
    "query='x', limit=5"
    """
    text = (
        args
        if isinstance(args, str)
        else ", ".join(
            f"{key}={_ARGUMENT_REPR.repr(value)}" for key, value in (args or {}).items()
        )
    )

    return truncate_line(text, max_chars)
