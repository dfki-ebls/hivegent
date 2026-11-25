"""What skills and assistants share: a library per scope, listed most specific first.

A user sees the items of every scope in the order user, groups, global.  A
name is unique only within its scope, so an item is referenced by its scope's
prefix and its name, ``/srv/<name>``, ``@<group>/<name>``, or ``~/<name>``,
as a document path is, and a reference resolves exactly or not at all.  No
scope can take over a name another one holds, whoever can see both.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import HTTPException

from .shared.l10n import Localized
from .shared.skills import valid_skill_name
from .store import (
    AccessDenied,
    GroupScope,
    Scope,
    User,
    UserScope,
    Workspace,
    split_scope_path,
)

__all__ = [
    "LibraryGrant",
    "LibraryScope",
    "Listed",
    "Scoped",
    "library_ref",
    "library_scopes",
    "resolve_ref",
    "split_library_ref",
    "visible",
    "writable_library",
]

type LibraryScope = GroupScope | UserScope
"""A scope whose library Hivegent keeps, every one but the global."""

_INVALID_REF = Localized(
    en="A reference names a scope and a name, such as ~/name, @group/name, or /srv/name",
    de="Ein Verweis nennt einen Bereich und einen Namen, etwa ~/name, @gruppe/name oder /srv/name",
)

_GLOBAL_ONLY = Localized(
    en="The global library comes from the operator and cannot be changed here",
    de="Die globale Bibliothek stellt der Betrieb bereit, sie lässt sich hier nicht ändern",
)


class Scoped(Protocol):
    """An item some scope holds under a name."""

    @property
    def name(self) -> str:
        """Its name, unique within its scope."""
        ...

    @property
    def scope(self) -> Scope:
        """Who owns it."""
        ...


def library_ref(item: Scoped) -> str:
    """The reference naming *item* exactly, its scope's prefix and its name.

    >>> from types import SimpleNamespace
    >>> from .store import GlobalScope
    >>> library_ref(SimpleNamespace(scope=GlobalScope(), name="legal"))
    '/srv/legal'
    >>> library_ref(SimpleNamespace(scope=UserScope("u"), name="legal"))
    '~/legal'
    """
    return item.scope.render(item.name)


def split_library_ref(ref: str) -> tuple[str, str]:
    """The scope prefix and the name *ref* names.

    >>> split_library_ref("@team/legal"), split_library_ref("/srv/legal")
    (('@team', 'legal'), ('/srv', 'legal'))

    Raises:
        ValueError: For no reference, such as a bare name.
    """
    try:
        prefix, name = split_scope_path(ref)
    except ValueError as exc:
        raise ValueError(_INVALID_REF.current) from exc

    if not valid_skill_name(name):
        raise ValueError(_INVALID_REF.current)

    return prefix, name


def resolve_ref(user: User, ref: str) -> tuple[Scope, str] | None:
    """The scope among the ones *user* sees and the name *ref* names, ``None`` for none."""
    try:
        prefix, name = split_library_ref(ref)
    except ValueError:
        return None

    scope = next((scope for scope in user.scopes() if scope.prefix == prefix), None)

    return None if scope is None else (scope, name)


@dataclass(frozen=True, slots=True)
class Listed[T: Scoped]:
    """An item as its user sees it.

    Attributes:
        item: The item.
        writable: Whether the user may change or delete it.
    """

    item: T
    writable: bool


def library_scopes(user: User, *, writable: bool) -> list[LibraryScope]:
    """The library scopes *user* reads, or writes when *writable* is true."""
    return [
        store.scope
        for store in user.workspaces(writable=writable)
        if isinstance(store.scope, GroupScope | UserScope)
    ]


@dataclass(frozen=True, slots=True)
class LibraryGrant:
    """The libraries of a scope, with the write grant of its workspace they are changed under.

    Attributes:
        store: The scope's workspace, granted for writing.
        scope: Its scope, one Hivegent keeps libraries for.

    Raises:
        AccessDenied: When *store* is not granted for writing, or is the
            global workspace or a source's.
    """

    store: Workspace
    scope: LibraryScope = field(init=False)

    def __post_init__(self) -> None:
        self.store.require_writable()

        match self.store.scope:
            case GroupScope() | UserScope() as scope:
                object.__setattr__(self, "scope", scope)
            case _:
                raise AccessDenied(_GLOBAL_ONLY.current)


def writable_library(user: User, prefix: str) -> LibraryGrant:
    """The libraries of the scope at *prefix*, ``~`` or ``@<group>``, which *user* changes.

    Raises:
        HTTPException: ``403`` for the global scope or one the user may not write.
    """
    try:
        return LibraryGrant(user.workspace(prefix, writable=True))
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def visible[T: Scoped](
    user: User, items_of: Callable[[Scope], Iterable[T]]
) -> list[Listed[T]]:
    """Every item *user* sees, their own first, then each group's, then global."""
    writes = set(library_scopes(user, writable=True))

    return [
        Listed(item, scope in writes)
        for scope in user.scopes()
        for item in items_of(scope)
    ]
