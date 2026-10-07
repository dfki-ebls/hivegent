"""Publishing workspace changes that never passed through a job.

Every surface that mutates without submitting a job has to notify, or clients
other than the one that asked stay stale.  The two helpers cover the two shapes
a caller comes in as: holding the store it wrote to, or holding the stores one
commit of the changeset gateway changed and what it moved and deleted.
"""

from collections.abc import Iterable

from .changes import PathChanges
from .jobs import manager
from .store import Casebase

__all__ = ["announce_commit", "notify_workspace_change"]


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


def announce_commit(
    owner: str,
    stores: Iterable[Casebase],
    changes: PathChanges,
    *,
    exclude_client: str | None = None,
) -> None:
    """Tell *owner*'s clients once that a commit changed *stores*, and what it moved and deleted.

    One event however many workspaces the commit spans, since a client follows
    its moves at once, and none for a commit that changed no workspace.

    *exclude_client* is the client that asked for the change and so learns
    what changed from its own response.
    """
    scopes = tuple(sorted({store.scope.prefix for store in stores}))

    if scopes:
        manager.notify_committed(owner, scopes, changes, exclude_client=exclude_client)
