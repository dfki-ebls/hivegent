"""Identity, and the user and group storage namespaces it grants.

:class:`User` is the request's identity, and the only source of the
casebases a request may touch: each carries the grant it was derived
with, so a store the user may only read can never be written
(:attr:`Casebase.writable`, enforced where every mutation takes its lock).

A :class:`Casebase` is an immutable identifier — ``(kind, id)`` — for
one user or group's slice of the system.  Only the workspace files
live on disk per casebase, under ``<data_dir>/workspace/<store_key>/``
where ``store_key`` is the same ``"user:<id>"`` / ``"group:<id>"`` token
that scopes the ``owner_*`` columns on ``documents``; chunk visibility
derives from the document FK — one identifier keys filesystem and SQL
end-to-end.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, cast, get_args

from .config import ADMIN_ROLE, sanitize_group_id, sanitize_user_id, settings
from .l10n import Localized
from .tools.base import Gated, SearchPath, SearchPathFilterFunc, canonical_local_path
from .tools.scope import PrefixScope

__all__ = [
    "GROUP_PREFIX",
    "USER_PREFIX",
    "AccessDenied",
    "Casebase",
    "CasebaseKind",
    "User",
    "WorkspaceScope",
    "build_search_paths",
]

CasebaseKind = Literal["user", "group"]

USER_PREFIX = "~"
GROUP_PREFIX = "@"

_GATED = Gated()


_NOT_A_MEMBER = Localized(
    en="Not a member of this group", de="Kein Mitglied dieser Gruppe"
)
_WRITE_ACCESS_REQUIRED = Localized(
    en="Write access required for this group",
    de="Für diese Gruppe ist Schreibzugriff erforderlich",
)
_READ_ONLY = Localized(
    en="This workspace is read-only for you",
    de="Dieser Arbeitsbereich ist für dich schreibgeschützt",
)


class AccessDenied(PermissionError):
    """A store was asked for, or asked to change, beyond what its grant allows."""


def _invalid_path(path: str) -> Localized[str]:
    return Localized(
        en=f"Invalid workspace path: {path!r}",
        de=f"Ungültiger Pfad im Arbeitsbereich: „{path}“",
    )


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
        return PrefixScope(self.prefix).render(local)

    def render_filter_entry(self, local: str) -> str:
        """Render a :class:`DocumentFilter` local entry as a canonical path.

        Filters use ``/`` for the whole store, which renders as the bare scope
        root with a trailing slash (``~/`` or ``@<group>/``); every other entry
        renders like any local path.
        """
        return f"{self.prefix}/" if local == "/" else self.render(local)

    def strip_prefix(self, raw: str) -> str | None:
        """Return *raw*'s local part if it addresses this scope, else ``None``."""
        return PrefixScope(self.prefix).strip_prefix(raw)

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
        raise ValueError(_invalid_path(raw).current)


@dataclass(slots=True, frozen=True)
class Casebase:
    """Identifies a casebase namespace (user or group).

    Each casebase owns a workspace directory at
    ``<data_dir>/workspace/<store_key>/`` and rows in the global SQL
    database scoped by ``owner_user_id`` / ``owner_group_id``.
    It is a :class:`~hivegent.workspace.operations.Root` of the changeset
    gateway, the one whose changes carry rows, an index, and announcements.

    *writable* is the grant the casebase was built with, and a personal one is
    always writable.  It takes no part in equality, so the same group read
    and written is one store, and it defaults to read, so a casebase built
    without a stated grant can never be changed.
    """

    kind: CasebaseKind
    id: str
    writable: bool = field(default=False, compare=False)

    @classmethod
    def for_user(cls, user_id: str) -> Self:
        """Build a user-scoped casebase, which its owner always writes."""
        return cls(kind="user", id=user_id, writable=True)

    @classmethod
    def for_group(cls, group_id: str, *, writable: bool = False) -> Self:
        """Build a group-scoped casebase with the grant the caller holds on it.

        Requests derive it from their :class:`User`, and only system code
        (admin, reconciliation) states a write grant of its own.
        """
        return cls(kind="group", id=group_id, writable=writable)

    @classmethod
    def for_owner(cls, user_id: str | None, group_id: str | None) -> Self:
        """Build the casebase owning a row, from its two owner FK columns.

        The ``documents`` table carries exactly one of the pair (its
        ``single_owner`` check constraint), so this is total for any row that
        is actually in the database.  It names the row's owner for a read and
        grants no write to a group.
        """
        if user_id is not None:
            return cls.for_user(user_id)
        if group_id is not None:
            return cls.for_group(group_id)
        raise ValueError("document row has neither owner set")

    @classmethod
    def from_store_key(cls, store_key: str, *, writable: bool) -> Self:
        """Parse a ``store_key`` back into a :class:`Casebase`.

        Inverse of :attr:`store_key`, for system code that states its own
        grant.  Raises :class:`ValueError` when the input is not a recognised
        ``user:<id>`` / ``group:<id>`` token.
        """
        kind, sep, identifier = store_key.partition(":")
        if not sep or kind not in get_args(CasebaseKind):
            raise ValueError(f"Invalid store_key: {store_key!r}")
        return cls(kind=cast(CasebaseKind, kind), id=identifier, writable=writable)

    def __post_init__(self) -> None:
        if self.kind == "user":
            sanitize_user_id(self.id)
        else:
            sanitize_group_id(self.id)

    def require_writable(self) -> None:
        """Refuse a change to a store granted for reading only.

        Raises:
            AccessDenied: When the store is not :attr:`writable`.
        """
        if not self.writable:
            raise AccessDenied(_READ_ONLY.current)

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

    @property
    def path(self) -> Path:
        """The workspace directory, without creating it, as a changeset root holds it."""
        return self.workspace_path(settings.data_dir)

    @property
    def policy(self) -> Gated:
        """How a change to it is asked about: gated, its ``.assets`` payloads reserved."""
        return _GATED

    @property
    def quota(self) -> None:
        """No cap on what it holds beyond the size of each file."""
        return None

    @property
    def store(self) -> Self:
        """Itself, the casebase whose rows, index, and announcements a change carries."""
        return self

    @staticmethod
    def workspace_root(data_dir: Path) -> Path:
        """Return the root directory holding every casebase workspace."""
        return data_dir / "workspace"

    def workspace_path(self, data_dir: Path) -> Path:
        """Return the workspace path without creating directories."""
        return self.workspace_root(data_dir) / self.store_key

    def locate(self, local: str) -> Path | None:
        """The absolute path *local* names in the workspace, ``None`` outside it.

        Resolved the way every tool path is
        (:func:`~hivegent.tools.base.canonical_local_path`), so neither ``..``
        nor a symlink leads a read out of the workspace.
        """
        root = self.path.resolve()
        canonical = canonical_local_path(root, local)

        return None if canonical is None else canonical[1]

    def workspace_dir(self, data_dir: Path) -> Path:
        """Return the workspace directory, creating it if needed.

        Contains source files, markdown companions, and recursive
        asset directories.  No other on-disk artifacts live here.
        """
        path = self.workspace_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(slots=True, frozen=True)
class User:
    """Authenticated user with group membership and roles.

    Groups model shared knowledge containers (read/write per group);
    roles model global capabilities.  Admin status is derived on every
    access from the fixed :data:`~hivegent.config.ADMIN_ROLE` being
    present in :attr:`roles` — no duplicate flag is stored anywhere.
    Grant admin by adding a user to that role in the IdP (or, for dev,
    auth is disabled and the local user is admin).

    Group membership lives solely in the OIDC token and is never
    persisted: every field here is reconstructed per request from the
    validated JWT claims.

    The casebases a request may touch are derived here and nowhere else
    (:attr:`store`, :meth:`group_stores`, :meth:`group_store`), each with the
    grant the token gives, so no caller can hand a run or a route a workspace
    the token does not grant, nor write one it may only read.

    Groups are held as IDs, which is what every path, workspace directory,
    and owner column uses.  ``group_labels`` carries the display names a
    provider additionally supplied, for the client to show in place of an
    opaque ID; nothing on the server reads it, so a rename upstream changes
    only what the UI renders.
    """

    id: str
    email: str | None = None
    name: str | None = None
    read_groups: frozenset[str] = field(default_factory=frozenset)
    write_groups: frozenset[str] = field(default_factory=frozenset)
    group_labels: Mapping[str, str] = field(default_factory=dict, compare=False)
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_groups(self) -> frozenset[str]:
        """Return all groups the user belongs to."""
        return self.read_groups | self.write_groups

    @property
    def is_admin(self) -> bool:
        """Whether the user holds the fixed admin role."""
        return ADMIN_ROLE in self.roles

    @property
    def store(self) -> Casebase:
        """The user's personal casebase."""
        return Casebase.for_user(self.id)

    def stores(self, *, writable: bool = False) -> tuple[Casebase, ...]:
        """The personal casebase followed by :meth:`group_stores`."""
        return (self.store, *self.group_stores(writable=writable))

    def group_stores(self, *, writable: bool = False) -> tuple[Casebase, ...]:
        """The group casebases the user may read, or with *writable* only write.

        Sorted, so a run's roots and the prompt naming them stay stable.
        """
        groups = self.write_groups if writable else self.all_groups

        return tuple(
            Casebase.for_group(group_id, writable=group_id in self.write_groups)
            for group_id in sorted(groups)
        )

    def group_store(self, group_id: str, *, writable: bool = False) -> Casebase:
        """The casebase of *group_id*, as the token grants it.

        Raises:
            ValueError: When *group_id* is no valid group ID.
            AccessDenied: When the user is no member, or *writable* asks for a
                grant the user only holds for reading.
        """
        store = Casebase.for_group(group_id, writable=group_id in self.write_groups)

        if group_id not in self.all_groups:
            raise AccessDenied(_NOT_A_MEMBER.current)

        if writable and not store.writable:
            raise AccessDenied(_WRITE_ACCESS_REQUIRED.current)

        return store


def build_search_paths(
    stores: Sequence[Casebase],
    data_dir: Path,
    *,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> tuple[SearchPath, ...]:
    """Build :class:`SearchPath` entries for *stores*, typically :meth:`User.stores`.

    Nothing is created: a mutation makes its own destination downstream and a
    read of a workspace nobody has written to yet finds nothing, so handing a
    tool its roots must not materialise an empty directory for every group the
    caller can reach.

    Args:
        stores: The casebases to search, the personal one first.
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
        for s in stores
    )
