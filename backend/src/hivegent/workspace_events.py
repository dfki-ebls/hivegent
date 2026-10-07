"""Announcing workspace changes that never passed through a job.

Every surface that mutates without submitting a job has to announce, or
clients other than the one that asked stay stale.  The changeset gateway
announces what each commit moved and deleted, and every other mutation the
scope it changed, both as one :class:`~hivegent.changes.WorkspaceChanged`.
"""

from .changes import WorkspaceChanged
from .jobs import manager

__all__ = ["announce_workspace_changed"]


def announce_workspace_changed(
    owner: str, changed: WorkspaceChanged, *, exclude_client: str | None = None
) -> None:
    """Tell *owner*'s clients once how the workspaces changed, and nothing when none did.

    *exclude_client* is the client that asked for the change and so learns
    what changed from its own response.
    """
    if changed.scopes:
        manager.notify_workspace_changed(owner, changed, exclude_client=exclude_client)
