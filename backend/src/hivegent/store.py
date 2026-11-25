"""Identity, the scopes it may see, and the workspaces it grants.

A :data:`Scope` names who owns something: the deployment (:class:`GlobalScope`),
a group (:class:`GroupScope`), or a user (:class:`UserScope`).  An external
source the operator configured (:class:`SourceScope`) is no owner but keys a
workspace the same way, mirrored from the source and seen by its audience.
Its key, its :attr:`~_ScopeBase.kind` with its id if it has one (``global``,
``group:<id>``, ``user:<id>``, ``source:<id>``), is the one token that keys
filesystem and SQL end-to-end, and its prefix (``/srv``, ``@<id>``, ``~``,
``/mnt/<id>``) the one that addresses a path in it.  :meth:`User.scopes` is the single
answer to which scopes a principal sees, in the order user, groups, global.

:class:`User` is the request's principal, and the only source of the
workspaces a request may touch: each carries the :class:`Grant` it was
derived with, so a store the user may only read, a source's mirror always
among them, can never be written (:meth:`Workspace.require_writable`,
enforced where every mutation takes its lock and in every SQL write).  A :class:`Workspace` has no public
constructor, so its grant comes from a :class:`User` or, for the host's own
work, from a :class:`HostGrant`.
Only the workspace files live on disk per workspace, under
``<data_dir>/workspace/<store_key>/``, and chunk visibility derives from the
document FK.
"""

from abc import ABC, abstractmethod
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar, Literal, Self

from exar.memory.mounts import canonical, render, strip

from .config import ADMIN_ROLE, sanitize_group_id, sanitize_user_id, settings
from .shared.l10n import Localized
from .tools.base import (
    CommitPolicy,
    ModeGated,
    PathFilter,
    Reviewed,
    SearchPath,
)

__all__ = [
    "GLOBAL_PREFIX",
    "SOURCE_PREFIX",
    "USER_PREFIX",
    "AccessDenied",
    "GlobalScope",
    "Grant",
    "GroupScope",
    "HostGrant",
    "Scope",
    "ScopeKind",
    "SourceScope",
    "User",
    "UserScope",
    "Workspace",
    "WorkspaceScope",
    "audience",
    "parse_scope",
    "split_scope_path",
]

USER_PREFIX = "~"
_GROUP_PREFIX = "@"
GLOBAL_PREFIX = "/srv"
"""The path prefix of the global workspace, the one place agents and clients learn it."""
SOURCE_PREFIX = "/mnt"
"""The directory every source's workspace is mounted in, as ``/mnt/<id>``."""

_GATED = ModeGated()
_REVIEWED = Reviewed()


_NOT_VISIBLE = Localized(
    en="You cannot access this workspace",
    de="Du hast keinen Zugriff auf diesen Arbeitsbereich",
)
_ADMIN_REQUIRED = Localized(
    en="Administrator privileges required", de="Adminrechte erforderlich"
)
_PEOPLE_ONLY = Localized(
    en="Not available to service accounts",
    de="Für Dienstkonten nicht verfügbar",
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


type ScopeKind = Literal["global", "group", "user", "source"]
"""What a scope is, the leading token of its key."""


class _ScopeBase(ABC):
    """The kind, key, and path prefix every scope answers, and the paths under it."""

    __slots__ = ()

    kind: ClassVar[ScopeKind]

    @property
    def key(self) -> str:
        """The token keying the workspace directory and its SQL row, its :attr:`kind`."""
        return self.kind

    @property
    @abstractmethod
    def prefix(self) -> str:
        """The leading token of every path in the scope."""

    def render(self, local: str) -> str:
        """Render *local* as a canonical path under this scope (empty = root)."""
        return render(self.prefix, local)

    def render_filter_entry(self, local: str, *, directory: bool) -> str:
        """Render a :class:`DocumentFilter` local entry as a canonical path for the model.

        A directory ends with a slash, the whole store being the bare scope
        root with one.
        """
        return f"{self.render(local)}/" if directory else self.render(local)


@dataclass(slots=True, frozen=True)
class _IdentifiedScope(_ScopeBase, ABC):
    """A scope of one of several of its kind, told apart by its id."""

    id: str

    @property
    def key(self) -> str:
        """``<kind>:<id>``."""
        return f"{self.kind}:{self.id}"


@dataclass(slots=True, frozen=True)
class GlobalScope(_ScopeBase):
    """The deployment's own scope, which every principal sees."""

    kind = "global"

    @property
    def prefix(self) -> str:
        """:data:`GLOBAL_PREFIX`."""
        return GLOBAL_PREFIX


@dataclass(slots=True, frozen=True)
class GroupScope(_IdentifiedScope):
    """A group's scope, named ``@<id>`` in paths."""

    kind = "group"

    def __post_init__(self) -> None:
        sanitize_group_id(self.id)

    @property
    def prefix(self) -> str:
        """``@<id>``."""
        return f"{_GROUP_PREFIX}{self.id}"


@dataclass(slots=True, frozen=True)
class UserScope(_IdentifiedScope):
    """A user's own scope, ambient and idless (``~``) in the paths its owner uses."""

    kind = "user"

    def __post_init__(self) -> None:
        sanitize_user_id(self.id)

    @property
    def prefix(self) -> str:
        """:data:`USER_PREFIX`."""
        return USER_PREFIX


type Scope = GlobalScope | GroupScope | UserScope
"""Who owns a workspace, and later any other scoped resource."""


@dataclass(slots=True, frozen=True)
class SourceScope(_IdentifiedScope):
    """An external source's mirror, mounted at ``/mnt/<id>`` for the audience the operator chose."""

    kind = "source"

    def __post_init__(self) -> None:
        if self.id not in settings.sources:
            raise ValueError(f"Unknown source: {self.id!r}")

    @property
    def prefix(self) -> str:
        """``/mnt/<id>``."""
        return f"{SOURCE_PREFIX}/{self.id}"


type WorkspaceScope = Scope | SourceScope
"""What keys a workspace: its owner's scope, or the source it mirrors."""


_IDENTIFIED = {cls.kind: cls for cls in (GroupScope, UserScope, SourceScope)}


def audience(groups: Collection[str]) -> tuple[Scope, ...]:
    """The scopes whose members see what is shared with *groups*, everyone's when there are none.

    >>> audience(["team"]), audience([])
    ((GroupScope(id='team'),), (GlobalScope(),))
    """
    return tuple(GroupScope(group) for group in groups) or (GlobalScope(),)


def parse_scope(key: str) -> WorkspaceScope:
    """Parse a scope's :attr:`~_ScopeBase.key` back into the scope.

    >>> parse_scope("global"), parse_scope("group:team")
    (GlobalScope(), GroupScope(id='team'))

    Raises:
        ValueError: When *key* is no scope key, or names an invalid id.
    """
    match key.partition(":"):
        case (GlobalScope.kind, "", ""):
            return GlobalScope()
        case (kind, ":", identifier) if (cls := _IDENTIFIED.get(kind)) is not None:
            return cls(identifier)
        case _:
            raise ValueError(f"Invalid scope key: {key!r}")


def split_scope_path(raw: str) -> tuple[str, str]:
    """Split a canonical path into its scope prefix and its local path.

    The boundary parser for the HTTP API and chat filters, which recover the
    scope from a path alone.  A bare scope root yields an empty local.

    >>> split_scope_path("~/a.md"), split_scope_path("@team"), split_scope_path("/srv/x")
    (('~', 'a.md'), ('@team', ''), ('/srv', 'x'))
    >>> split_scope_path("/mnt/wiki/a.md")
    ('/mnt/wiki', 'a.md')

    Raises:
        ValueError: When *raw* carries no scope prefix.
    """
    for prefix in (USER_PREFIX, GLOBAL_PREFIX):
        if (local := strip(prefix, raw)) is not None:
            return prefix, local

    for prefix in (_GROUP_PREFIX, f"{SOURCE_PREFIX}/"):
        if raw.startswith(prefix):
            name, _, local = raw[len(prefix) :].partition("/")

            if name:
                return f"{prefix}{name}", local

    raise ValueError(_invalid_path(raw).current)


class Grant(Enum):
    """What the holder of a :class:`Workspace` may do to it."""

    READ = "read"
    WRITE = "write"


@dataclass(slots=True, frozen=True, init=False)
class Workspace:
    """The workspace of one :data:`WorkspaceScope`, with the grant it was built with.

    Each workspace owns a directory at ``<data_dir>/workspace/<store_key>/``,
    a source's its mirror at ``<data_dir>/sources/<id>/``, and the
    ``documents`` rows naming its ``workspaces`` row, both keyed by its scope.
    Its :class:`~hivegent.workspace.operations.Root` (``Root.of``) is the one
    of the changeset gateway whose changes carry rows, an index, and
    announcements.

    It has no public constructor, and neither ``dataclasses.replace`` nor a
    copy changes its grant: :class:`User` derives every grant a request
    holds and :class:`HostGrant` the host's.  *grant* takes no part in
    equality, so the same store read and written is one store.
    """

    scope: WorkspaceScope
    grant: Grant = field(compare=False)

    @classmethod
    def _granted(cls, scope: WorkspaceScope, grant: Grant) -> Self:
        """The one constructor, for :class:`User` and :class:`HostGrant`."""
        store = object.__new__(cls)
        object.__setattr__(store, "scope", scope)
        object.__setattr__(store, "grant", grant)

        return store

    @property
    def writable(self) -> bool:
        """Whether its holder may change it."""
        return self.grant is Grant.WRITE

    def require_writable(self) -> None:
        """Refuse a change to a store not granted for writing, as every mutation's lock and SQL write do.

        Raises:
            AccessDenied: When the store is not granted :attr:`Grant.WRITE`.
        """
        if not self.writable:
            raise AccessDenied(_READ_ONLY.current)

    @property
    def store_key(self) -> str:
        """Its scope's key, for the filesystem, SQL, and vector index scoping."""
        return self.scope.key

    @property
    def path(self) -> Path:
        """The workspace directory, without creating it, a source's its mirror."""
        if isinstance(self.scope, SourceScope):
            return settings.data_dir / "sources" / self.scope.id

        return self.workspace_root() / self.store_key

    @property
    def policy(self) -> CommitPolicy:
        """How a change to it is asked about, set by its scope's kind alone.

        Every workspace a user may write, their own, a group's, or the global
        one, is :class:`ModeGated`, approved where the mode asks, so ``write``
        mode changes any of them without asking, as the user chose it to.
        A source's mirror is only ever changed by the host's sync, which
        approves nothing, so it is :class:`Reviewed`.
        Both reserve the ``.assets`` payloads.
        """
        return _REVIEWED if isinstance(self.scope, SourceScope) else _GATED

    @staticmethod
    def workspace_root() -> Path:
        """Return the root directory holding every workspace but the sources' mirrors."""
        return settings.data_dir / "workspace"

    def search_path(self, filter_func: PathFilter | None = None) -> SearchPath:
        """The root a tool resolves this workspace's paths against, without creating it."""
        return SearchPath.of(
            path=self.path,
            target=self.scope.prefix,
            implied=isinstance(self.scope, UserScope),
            filter_func=filter_func,
            policy=self.policy,
        )

    def locate(self, local: str) -> Path | None:
        """The absolute path *local* names in the workspace, ``None`` outside it.

        Resolved the way every tool path is
        (:func:`exar.memory.mounts.canonical`), so ``..`` never leads a
        read out of the workspace and a path through a symlink is none.
        """
        found = canonical(self.path, local)

        return None if found is None else found[1]

    def workspace_dir(self) -> Path:
        """Return the workspace directory, creating it if needed.

        Contains source files, markdown companions, and recursive
        asset directories.  No other on-disk artifacts live here.
        """
        path = self.path
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(slots=True, frozen=True)
class HostGrant:
    """The host acting for itself, with no principal, which writes every workspace.

    The CLI and the jobs the host runs (reconciliation, a source's sync)
    build one and hand it down, and nothing a request reaches does, as
    ``test_access_scope`` enforces, so a request holds only what its
    :class:`User` grants.
    """

    def workspace(self, scope: WorkspaceScope) -> Workspace:
        """The workspace of *scope*, granted for writing."""
        return Workspace._granted(scope, Grant.WRITE)


type _PrincipalKind = Literal["person", "service"]
"""A person signs in, a service (the Teams bridge) acts for many people at once."""


@dataclass(slots=True, frozen=True)
class User:
    """Authenticated principal with group membership and roles.

    Groups model shared knowledge containers (read/write per group);
    roles model global capabilities.  Admin status is derived on every
    access from the fixed :data:`~hivegent.config.ADMIN_ROLE` being
    present in :attr:`roles`, no duplicate flag is stored anywhere.

    Group membership lives solely in the OIDC token and is never
    persisted: every field here is reconstructed per request from the
    validated JWT claims.

    The scopes a principal sees (:meth:`scopes`) and the workspaces it may
    touch (:meth:`workspaces`, :meth:`workspace`) are derived here and
    nowhere else, each workspace with the grant the token gives, so no caller
    can hand a run or a route a workspace the token does not grant, nor write
    one it may only read.  A service principal sees the global scope alone,
    holds no role, and writes nothing, whatever its token claims.

    Groups are held as IDs, which is what every path, workspace directory,
    and workspace key uses.  ``group_labels`` carries the display names a
    provider additionally supplied, for the client to show in place of an
    opaque ID; nothing on the server reads it, so a rename upstream changes
    only what the UI renders.

    ``expires_at`` is the UNIX time until which the token vouches for these
    grants, so work outliving the request stops trusting them then, ``None``
    for a principal no token authorizes, such as the development user.
    """

    id: str
    email: str | None = None
    name: str | None = None
    read_groups: frozenset[str] = field(default_factory=frozenset)
    write_groups: frozenset[str] = field(default_factory=frozenset)
    group_labels: Mapping[str, str] = field(default_factory=dict, compare=False)
    roles: frozenset[str] = field(default_factory=frozenset)
    kind: _PrincipalKind = "person"
    expires_at: float | None = field(default=None, compare=False)

    def holds(self, other: User) -> bool:
        """Whether this principal holds every grant of *other*, so it may continue what *other* started.

        >>> User("a", write_groups=frozenset({"x"})).holds(User("a", read_groups=frozenset({"x"})))
        True
        >>> User("a", read_groups=frozenset({"x"})).holds(User("a", write_groups=frozenset({"x"})))
        False
        """
        return (
            self.id == other.id
            and self.kind == other.kind
            and other.all_groups <= self.all_groups
            and other.write_groups <= self.write_groups
            and other.roles <= self.roles
        )

    @property
    def is_service(self) -> bool:
        """Whether this is a service principal rather than a person."""
        return self.kind == "service"

    @property
    def all_groups(self) -> frozenset[str]:
        """Return all groups the principal belongs to, none for a service."""
        return frozenset() if self.is_service else self.read_groups | self.write_groups

    @property
    def is_admin(self) -> bool:
        """Whether a person holds the fixed admin role."""
        return not self.is_service and ADMIN_ROLE in self.roles

    def require_admin(self) -> None:
        """Refuse a principal that is no :attr:`is_admin`.

        Raises:
            AccessDenied: When the principal is no admin.
        """
        if not self.is_admin:
            raise AccessDenied(_ADMIN_REQUIRED.current)

    def require_person(self) -> None:
        """Refuse a service principal, for what belongs to one person alone.

        Raises:
            AccessDenied: When the principal is a service.
        """
        if self.is_service:
            raise AccessDenied(_PEOPLE_ONLY.current)

    def scopes(self) -> tuple[Scope, ...]:
        """Every scope the principal sees: its own, its groups sorted, then global.

        The one resolution of visibility that every scoped resource reuses, in
        the order a more specific scope precedes a broader one.
        """
        if self.is_service:
            return (GlobalScope(),)

        return (
            UserScope(self.id),
            *(GroupScope(group_id) for group_id in sorted(self.all_groups)),
            GlobalScope(),
        )

    def sees(self, groups: Collection[str]) -> bool:
        """Whether the principal is in the :func:`audience` of *groups*."""
        return not set(audience(groups)).isdisjoint(self.scopes())

    def _writes(self, scope: Scope) -> bool:
        """Whether the token grants writing *scope*, one of :meth:`scopes`."""
        match scope:
            case GlobalScope():
                return self.is_admin
            case GroupScope(id=group_id):
                return group_id in self.write_groups
            case UserScope():
                return True

    def _sources(self) -> Iterator[Workspace]:
        """The mirror of every source whose audience the principal is in.

        A source with no ``groups`` is the global scope's.  A source is
        read-only by design, so the principal only ever reads its mirror,
        which the host's sync alone changes.
        """
        for source_id, source in settings.sources.items():
            if self.sees(source.groups):
                yield Workspace._granted(SourceScope(source_id), Grant.READ)

    def workspaces(self, *, writable: bool = False) -> tuple[Workspace, ...]:
        """The workspace of every scope and source the principal sees, or with *writable* only those it changes."""
        granted = (
            *(
                Workspace._granted(
                    scope, Grant.WRITE if self._writes(scope) else Grant.READ
                )
                for scope in self.scopes()
            ),
            *self._sources(),
        )

        return tuple(store for store in granted if store.writable or not writable)

    def workspace(self, prefix: str, *, writable: bool = False) -> Workspace:
        """The workspace whose paths lead with *prefix*, as the token grants it.

        Raises:
            AccessDenied: When the principal does not see it, or *writable*
                asks for a grant it only holds for reading.
        """
        store = next((s for s in self.workspaces() if s.scope.prefix == prefix), None)

        if store is None:
            raise AccessDenied(_NOT_VISIBLE.current)

        if writable and not store.writable:
            raise AccessDenied(_READ_ONLY.current)

        return store

    @property
    def home(self) -> Workspace:
        """The personal workspace.

        Raises:
            AccessDenied: For a service principal, which has none.
        """
        return self.workspace(USER_PREFIX)
