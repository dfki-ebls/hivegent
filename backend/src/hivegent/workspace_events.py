"""Publishing workspace changes that never passed through a job.

Every surface that mutates without submitting a job has to notify, or clients
other than the one that asked stay stale.  The two helpers cover the two shapes
a caller comes in as: holding the store it wrote to, or holding only the
canonical path it was handed.
"""

from collections.abc import Awaitable, Callable
from typing import Concatenate

from .entries import is_scratch_path
from .jobs import manager
from .store import Casebase, WorkspaceScope

__all__ = ["announce_paths", "announcing_mutator", "notify_workspace_change"]


def notify_workspace_change(
    owner: str, store: Casebase, exclude_client: str | None = None
) -> None:
    """Tell *owner*'s clients that *store*'s workspace changed.

    *exclude_client* is the client that asked for the change and so re-reads
    the workspace on its own; every other client of *owner* needs telling.
    """
    manager.notify_scope_changed(
        owner, store.scope.prefix, exclude_client=exclude_client
    )


def announce_paths(owner: str, *paths: str) -> None:
    """Tell *owner*'s clients that the workspaces *paths* name have changed.

    Deduplicated by workspace, so a move whose two ends share one announces
    once and one crossing between two announces both.

    A scratch path is the one that stays quiet: the tree hides `.scratch/`, so
    the refresh it would trigger costs every other tab a re-read of a workspace
    that looks exactly as it did.  A run parking its own state there is the
    common case, not the rare one, which is what makes the difference worth
    drawing.
    """
    for prefix in {
        scope.prefix
        for scope, local in map(WorkspaceScope.parse, paths)
        if not is_scratch_path(local)
    }:
        manager.notify_scope_changed(owner, prefix)


def announcing_mutator[**P, R](
    mutator: Callable[Concatenate[str, P], Awaitable[R]],
    owner: str,
) -> Callable[Concatenate[str, P], Awaitable[R]]:
    """Wrap a canonical-path mutation so a success notifies *owner*.

    Sits directly around the mutator the write tools are built with, the
    narrowest place that sees both the path and the fact that the write
    succeeded — the layers below hold a :class:`Casebase`, whose id is the
    *group* for a shared workspace, so none of them can name the user to tell.
    A mutation with more than one end announces through :func:`announce_paths`
    itself, since only it knows how many paths it changed.
    """

    async def run(path: str, *args: P.args, **kwargs: P.kwargs) -> R:
        result = await mutator(path, *args, **kwargs)
        announce_paths(owner, path)
        return result

    return run
