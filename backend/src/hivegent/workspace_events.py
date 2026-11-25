"""Announcing workspace and library changes to every client that can see them.

The changeset gateway announces each commit with the paths it moved and
deleted, and every other mutation the workspaces it changed, or clients stay
stale.  A client follows the workspaces its user may read, so a change to the
global workspace reaches everyone, one to a group workspace every member,
whoever made it, and one to a personal workspace only its owner.  A scope's
skill or assistant library reaches the same clients its workspace does.
"""

from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import Literal

from .jobs import LibraryChanged, manager
from .store import Scope, User, Workspace, split_scope_path
from .tools.changes import WorkspaceChanged

__all__ = ["announce_library_changed", "announce_workspace_changed", "follows"]


def follows(user: User) -> Mapping[str, str]:
    """The topics a feed of *user* follows, every workspace it may read by ``store_key`` and prefix."""
    return {store.store_key: store.scope.prefix for store in user.workspaces()}


def announce_workspace_changed(
    stores: Collection[Workspace], changed: WorkspaceChanged | None = None
) -> None:
    """Tell every client following one of *stores* what *changed* shows it.

    *changed* names its scopes and paths by the prefixes of *stores*, of which
    only one is personal, the actor's, and defaults to each store as a whole.
    """
    keys = {store.scope.prefix: store.store_key for store in stores}

    if not keys:
        return

    announced = changed or WorkspaceChanged(tuple(sorted(keys)))
    manager.notify(lambda followed: _visible(announced, keys, followed))


def announce_library_changed(
    kind: Literal["skills", "assistants"], scope: Scope
) -> None:
    """Tell every client following the workspace of *scope* that its library of *kind* changed."""
    event = LibraryChanged(kind=kind)
    manager.notify(lambda followed: event if scope.key in followed else None)


def _visible(
    changed: WorkspaceChanged, keys: Mapping[str, str], followed: Mapping[str, str]
) -> WorkspaceChanged | None:
    """What a client following *followed* sees of *changed*, nothing when none of it.

    A move out of its sight is a delete, and one into it only a change of the
    destination's scope.  It keeps the id of *changed*.
    """

    def seen(path: str) -> bool:
        key = keys.get(split_scope_path(path)[0])

        return key is not None and key in followed

    scopes = tuple(scope for scope in changed.scopes if seen(scope))

    if not scopes:
        return None

    if len(scopes) == len(changed.scopes):
        return changed

    moves = tuple(m for m in changed.moves if seen(m.source) and seen(m.destination))
    left = (m.source for m in changed.moves if not seen(m.destination))
    deletes = tuple(path for path in (*changed.deletes, *left) if seen(path))

    return replace(changed, scopes=scopes, moves=moves, deletes=deletes)
