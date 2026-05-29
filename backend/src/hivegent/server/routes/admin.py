"""Admin-only routes for destructive resets and operator overviews.

Every endpoint in this router gates on :func:`hivegent.auth.require_admin`
via the router-level dependency.  The actions mirror the destructive
operations exposed in open-webui's admin panel, mapped onto Hivegent's
storage layout:

* Workspace files live in ``<data_dir>/workspace/<store_key>/``
* PostgreSQL is the source of truth for documents, chunks (text +
  vector), conversations, tokens, memory, users, groups, and group
  membership.

A full ``POST /admin/reset/factory`` wipes workspace + database and
leaves the deployment in the same state as a clean checkout.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete

from ... import workspace
from ...auth import require_admin
from ...config import settings
from ...db.documents import delete_all_documents
from ...db.engine import session
from ...db.groups import delete_all_groups, list_groups_with_counts
from ...db.models import Group, User
from ...db.users import delete_all_users, delete_user, list_users_with_counts
from ...reconcile import reconcile_all
from ...store import Casebase
from ...types import (
    AdminFactoryResetResponse,
    AdminGroupInfo,
    AdminListGroupsResponse,
    AdminListUsersResponse,
    AdminReindexResponse,
    AdminResetResponse,
    AdminUserInfo,
    BulkDeleteUserDataResponse,
)

__all__ = ["router"]

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


# ─── Overviews ────────────────────────────────────────────────────────


def _workspace_children() -> set[str]:
    """Return the set of casebase ``store_key`` names present on disk."""
    workspace_root = settings.data_dir / "workspace"
    if not workspace_root.exists():
        return set()
    return {child.name for child in workspace_root.iterdir() if child.is_dir()}


@router.get("/users")
async def list_users() -> AdminListUsersResponse:
    """List users that have left a footprint in the local database."""
    rows, on_disk = await asyncio.gather(
        list_users_with_counts(),
        asyncio.to_thread(_workspace_children),
    )
    users = [
        AdminUserInfo(
            id=user_id,
            email=email,
            name=display_name,
            document_count=docs,
            conversation_count=convs,
            has_workspace=Casebase.for_user(user_id).store_key in on_disk,
        )
        for user_id, email, display_name, docs, convs in rows
    ]
    return AdminListUsersResponse(users=users)


@router.get("/groups")
async def list_groups() -> AdminListGroupsResponse:
    """List every group materialised in the local database."""
    rows, on_disk = await asyncio.gather(
        list_groups_with_counts(),
        asyncio.to_thread(_workspace_children),
    )
    groups = [
        AdminGroupInfo(
            id=group_id,
            document_count=docs,
            member_count=members,
            has_workspace=Casebase.for_group(group_id).store_key in on_disk,
        )
        for group_id, docs, members in rows
    ]
    return AdminListGroupsResponse(groups=groups)


# ─── System-wide resets ───────────────────────────────────────────────


@router.post("/reset/workspace")
async def admin_reset_workspace() -> AdminResetResponse:
    """Wipe every workspace file and document row.

    Chunks (text + vector) cascade with the document rows.
    Conversations, tokens, memory, users, and groups are kept.
    """
    await workspace.delete_workspace_root()
    await delete_all_documents()
    return AdminResetResponse(
        action="reset_workspace",
        message="Workspace files and document rows cleared",
    )


@router.post("/reset/database")
async def admin_reset_database() -> AdminResetResponse:
    """Drop every user, group, and the rows that cascade from them.

    Cascade chain (see ``backend/src/hivegent/db/models.py``):
    users → tokens, memory, conversations (→ messages), documents
    (→ chunks), group memberships; groups → group documents and
    memberships.  The workspace tree on disk is not touched.

    Both deletes run in a single session so a crash between them cannot
    leave the database in a half-wiped state.
    """
    async with session() as s:
        await s.execute(delete(User))
        await s.execute(delete(Group))
    return AdminResetResponse(
        action="reset_database",
        message="All users, groups, and dependent rows removed",
    )


@router.post("/reindex")
async def admin_reindex() -> AdminReindexResponse:
    """Reconcile every casebase: prune disk and SQL orphans."""
    reports = await reconcile_all()
    return AdminReindexResponse(
        stores_reconciled=len(reports),
        message=f"Reconciled {len(reports)} casebase(s)",
    )


@router.post("/reset/factory")
async def admin_factory_reset() -> AdminFactoryResetResponse:
    """Composite full reset: workspace + SQL.

    Idempotent — re-running on a clean tree is a no-op.  Leaves the
    deployment in the same shape as a fresh checkout.
    """
    await workspace.delete_workspace_root()
    await delete_all_users()
    await delete_all_groups()
    return AdminFactoryResetResponse(
        actions=["reset_workspace", "reset_database"],
        message="Factory reset complete",
    )


# ─── Targeted wipes ───────────────────────────────────────────────────


def _casebase_or_400(kind: str, identifier: str) -> Casebase:
    """Construct a casebase from a URL identifier, mapping invalid IDs to 400.

    ``Casebase.__post_init__`` already sanitizes; this just maps its
    ``ValueError`` to ``HTTPException(400)`` so callers don't need to.
    """
    try:
        return (
            Casebase.for_user(identifier)
            if kind == "user"
            else Casebase.for_group(identifier)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/data")
async def admin_delete_user_data(user_id: str) -> BulkDeleteUserDataResponse:
    """Wipe all data owned by a single user.

    ``workspace.delete_all`` clears documents (cascades to chunks via
    FK ``ON DELETE CASCADE``) and the workspace directory.  Removing
    the ``User`` row then cascades to tokens, memory, and
    conversations.  The user re-materialises lazily on the next
    request.
    """
    store = _casebase_or_400("user", user_id)
    await workspace.delete_all(store)
    await delete_user(store.id)
    return BulkDeleteUserDataResponse(
        message=f"All data for user {store.id!r} deleted",
    )


@router.delete("/groups/{group_id}/data")
async def admin_delete_group_data(group_id: str) -> BulkDeleteUserDataResponse:
    """Wipe all data owned by a single group.

    Removes the group's workspace directory and SQL document rows
    (which cascade to chunks).  Membership rows are kept so
    re-creating the group is a no-op for the IdP-side group
    assignment.
    """
    store = _casebase_or_400("group", group_id)
    await workspace.delete_all(store)
    return BulkDeleteUserDataResponse(
        message=f"All data for group {store.id!r} deleted",
    )
