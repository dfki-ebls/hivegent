"""Publishing workspace changes that never passed through a job.

Every surface that mutates without submitting a job has to notify, or clients
other than the one that asked stay stale.  The two helpers cover the two shapes
a caller comes in as: holding the store it wrote to, or holding the canonical
paths it changed, which is how the changeset gateway announces every commit.
"""

from .jobs import manager
from .store import Casebase, WorkspaceScope

__all__ = ["announce_paths", "notify_workspace_change"]


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


def announce_paths(
    owner: str, *paths: str, exclude_client: str | None = None
) -> None:
    """Tell *owner*'s clients that the workspaces *paths* name have changed.

    Deduplicated by workspace, so a change whose paths share one announces
    once and one crossing between two announces both.

    *exclude_client* is the client that asked for the change and so re-reads
    the workspace on its own.
    """
    for prefix in {WorkspaceScope.parse(path)[0].prefix for path in paths}:
        manager.notify_scope_changed(owner, prefix, exclude_client=exclude_client)
