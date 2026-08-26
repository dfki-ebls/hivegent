"""Casebase identity for user and group storage namespaces.

A :class:`Casebase` is an immutable identifier — ``(kind, id)`` — for
one user or group's slice of the system.  Only the workspace files
live on disk per casebase, under ``<data_dir>/workspace/<store_key>/``
where ``store_key`` is the same ``"user:<id>"`` / ``"group:<id>"`` token
that scopes the ``owner_*`` columns on ``documents``; chunk visibility
derives from the document FK — one identifier keys filesystem and SQL
end-to-end.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Concatenate, Literal, Self, cast, get_args

from .config import sanitize_group_id, sanitize_user_id
from .tools.base import SearchPath, SearchPathFilterFunc

__all__ = [
    "GROUP_PREFIX",
    "USER_PREFIX",
    "Casebase",
    "CasebaseKind",
    "WorkspaceScope",
    "build_search_paths",
    "scoped_operation",
]

CasebaseKind = Literal["user", "group"]

USER_PREFIX = "~"
GROUP_PREFIX = "@"


@dataclass(slots=True, frozen=True)
class WorkspaceScope:
    """Concrete ``~`` / ``@<group>`` workspace addressing scope.

    The personal workspace is ambient and idless (``~``); a group is named
    (``@<group_id>``). This is the single home of the prefix convention shared
    end-to-end (tool outputs, citations, chat filters, the HTTP API); the
    generic tools layer sees only the :class:`~hivegent.tools.scope.Scope`
    protocol this satisfies.
    """

    group_id: str | None = None

    @property
    def prefix(self) -> str:
        """Leading token rendered into a path: ``~`` or ``@<group_id>``."""
        return (
            USER_PREFIX if self.group_id is None else f"{GROUP_PREFIX}{self.group_id}"
        )

    def render(self, local: str) -> str:
        """Render *local* as a canonical path under this scope (empty = root)."""
        return f"{self.prefix}/{local}" if local else self.prefix

    def render_filter_entry(self, local: str) -> str:
        """Render a :class:`DocumentFilter` local entry as a canonical path.

        Filters use ``/`` for the whole store, which renders as the bare scope
        root with a trailing slash (``~/`` or ``@<group>/``); every other entry
        renders like any local path.
        """
        return f"{self.prefix}/" if local == "/" else self.render(local)

    def strip_prefix(self, raw: str) -> str | None:
        """Return *raw*'s local part if it addresses this scope, else ``None``."""
        if raw == self.prefix:
            return ""
        tag = f"{self.prefix}/"
        return raw[len(tag) :] if raw.startswith(tag) else None

    @classmethod
    def parse(cls, raw: str) -> tuple[Self, str]:
        """Parse a canonical path into ``(scope, local)`` at a trust boundary.

        The boundary parser for the HTTP API and chat filters: it recovers the
        scope from a path with no scope object in hand. A bare scope root yields
        an empty local.

        Raises:
            ValueError: When *raw* carries no recognized scope prefix.
        """
        if raw == USER_PREFIX or raw.startswith(f"{USER_PREFIX}/"):
            return cls(), raw[len(USER_PREFIX) + 1 :]
        if raw.startswith(GROUP_PREFIX):
            group_id, _, local = raw[len(GROUP_PREFIX) :].partition("/")
            if group_id:
                return cls(group_id), local
        raise ValueError(f"Invalid workspace path: {raw!r}")


@dataclass(slots=True, frozen=True)
class Casebase:
    """Identifies a casebase namespace (user or group).

    Each casebase owns a workspace directory at
    ``<data_dir>/workspace/<store_key>/`` and rows in the global SQL
    database scoped by ``owner_user_id`` / ``owner_group_id``.
    """

    kind: CasebaseKind
    id: str

    @classmethod
    def for_user(cls, user_id: str) -> Self:
        """Build a user-scoped casebase."""
        return cls(kind="user", id=user_id)

    @classmethod
    def for_group(cls, group_id: str) -> Self:
        """Build a group-scoped casebase."""
        return cls(kind="group", id=group_id)

    @classmethod
    def for_owner(cls, user_id: str | None, group_id: str | None) -> Self:
        """Build the casebase owning a row, from its two owner FK columns.

        The ``documents`` table carries exactly one of the pair (its
        ``single_owner`` check constraint), so this is total for any row that
        is actually in the database.
        """
        if user_id is not None:
            return cls.for_user(user_id)
        if group_id is not None:
            return cls.for_group(group_id)
        raise ValueError("document row has neither owner set")

    @classmethod
    def from_store_key(cls, store_key: str) -> Self:
        """Parse a ``store_key`` back into a :class:`Casebase`.

        Inverse of :attr:`store_key`.  Raises :class:`ValueError` when
        the input is not a recognised ``user:<id>`` / ``group:<id>``
        token.
        """
        kind, sep, identifier = store_key.partition(":")
        if not sep or kind not in get_args(CasebaseKind):
            raise ValueError(f"Invalid store_key: {store_key!r}")
        return cls(kind=cast(CasebaseKind, kind), id=identifier)

    def __post_init__(self) -> None:
        if self.kind == "user":
            sanitize_user_id(self.id)
        else:
            sanitize_group_id(self.id)

    @property
    def store_key(self) -> str:
        """Stable opaque key for the filesystem, SQL, and vector index scoping."""
        return f"{self.kind}:{self.id}"

    @property
    def scope(self) -> WorkspaceScope:
        """Addressing scope (``~`` personal / ``@<group>``) for this store.

        Bridges the storage identity to the :class:`WorkspaceScope` that renders
        canonical paths and routes incoming ones back to this workspace.
        """
        return WorkspaceScope(self.id) if self.kind == "group" else WorkspaceScope()

    @staticmethod
    def workspace_root(data_dir: Path) -> Path:
        """Return the root directory holding every casebase workspace."""
        return data_dir / "workspace"

    def workspace_path(self, data_dir: Path) -> Path:
        """Return the workspace path without creating directories."""
        return self.workspace_root(data_dir) / self.store_key

    def workspace_dir(self, data_dir: Path) -> Path:
        """Return the workspace directory, creating it if needed.

        Contains source files, markdown companions, and recursive
        asset directories.  No other on-disk artifacts live here.
        """
        path = self.workspace_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


def build_search_paths(
    store: Casebase,
    group_stores: Sequence[Casebase],
    data_dir: Path,
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> tuple[SearchPath, ...]:
    """Build :class:`SearchPath` entries for a user store and its groups.

    Nothing is created: a mutation makes its own destination downstream and a
    read of a workspace nobody has written to yet finds nothing, so handing a
    tool its roots must not materialise an empty directory for every group the
    caller can reach.

    Args:
        store: The user's personal casebase.
        group_stores: Group casebases the user can access.
        data_dir: Application data root directory.
        filter_for_store: Optional callable returning a file filter for
            each store.
    """
    get_filter = filter_for_store or (lambda _: None)
    return tuple(
        SearchPath(
            path=s.workspace_path(data_dir),
            scope=s.scope,
            filter_func=get_filter(s),
        )
        for s in (store, *group_stores)
    )


def scoped_operation[**P, R](
    operation: Callable[Concatenate[Casebase, str, P], Awaitable[R]],
    stores: Sequence[Casebase],
) -> Callable[Concatenate[str, P], Awaitable[R]]:
    """Front a ``(store, local_path, ...)`` operation with a canonical path.

    Every workspace operation takes the store and the path local to it, while a
    tool is handed one canonical path (``~/notes.md``, ``@team/notes.md``) that
    names both.  This routes the one back to the other — the inverse of what
    :func:`build_search_paths` renders into tool output — so an operation
    reaches whichever of *stores* the caller addressed instead of a single one
    bound up front.  A path naming a store outside *stores* raises, since the
    caller may not reach it.
    """

    async def run(path: str, *args: P.args, **kwargs: P.kwargs) -> R:
        scope, local = WorkspaceScope.parse(path)
        store = next((s for s in stores if s.scope == scope), None)
        if store is None:
            raise ValueError(f"No accessible workspace for {path!r}")

        return await operation(store, local, *args, **kwargs)

    return run
