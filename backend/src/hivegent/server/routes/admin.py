"""Admin-only routes for destructive resets and operator overviews.

Every endpoint in this router gates on :func:`hivegent.auth.require_admin`
via the router-level dependency.  The actions mirror the destructive
operations exposed in open-webui's admin panel, mapped onto Hivegent's
storage layout:

* Workspace files live in ``<data_dir>/workspace/<store_key>/`` and
  are the source of truth for document content; the ``documents`` and
  ``chunks`` rows (text + vector) are an index derived from them.
* PostgreSQL is authoritative for everything else: conversations,
  memory, users, and groups.  Group membership is not stored — it
  lives solely in the OIDC token.

A full ``POST /admin/reset/factory`` wipes workspace + database and
leaves the deployment in the same state as a clean checkout.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ... import workspace
from ...auth import require_admin
from ...conversation_files import reset_conversation_files
from ...db.application_settings import write_maintenance_enabled
from ...db.documents import delete_all_documents
from ...db.groups import list_groups_with_counts
from ...db.users import delete_all_identities, list_users_with_counts
from ...humanize import pluralize
from ...l10n import Localized
from ...reconcile import reconcile_all
from ...store import Casebase, User
from ...types import (
    AdminGroupInfo,
    AdminMaintenanceState,
    AdminOverviewResponse,
    AdminReindexResponse,
    AdminUserInfo,
)
from ..common import delete_user_data
from ..maintenance import is_enabled, set_enabled

__all__ = ["router"]

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _reindexed(stores: int, synced: int, renamed: int) -> Localized[str]:
    return Localized(
        en=(
            f"Reconciled {stores} {pluralize(stores, 'casebase')}: "
            f"{synced} {pluralize(synced, 'entry', 'entries')} synced, "
            f"{renamed} {pluralize(renamed, 'path')} renamed"
        ),
        de=(
            f"{stores} {pluralize(stores, 'Arbeitsbereich', 'Arbeitsbereiche')} "
            f"abgeglichen: {synced} {pluralize(synced, 'Eintrag', 'Einträge')} "
            f"aktualisiert, {renamed} {pluralize(renamed, 'Pfad', 'Pfade')} umbenannt"
        ),
    )


# ─── Overviews ────────────────────────────────────────────────────────


@router.get("/overview")
async def get_overview() -> AdminOverviewResponse:
    """List the users that left a footprint in the local database, and every group."""
    users, groups = await asyncio.gather(
        list_users_with_counts(), list_groups_with_counts()
    )
    return AdminOverviewResponse(
        users=[
            AdminUserInfo(id=user_id, document_count=docs, conversation_count=convs)
            for user_id, docs, convs in users
        ],
        groups=[
            AdminGroupInfo(id=group_id, document_count=docs)
            for group_id, docs in groups
        ],
    )


# ─── Maintenance mode ─────────────────────────────────────────────────


@router.get("/maintenance")
async def get_maintenance(request: Request) -> AdminMaintenanceState:
    """Report the current state of the global maintenance flag."""
    return AdminMaintenanceState(enabled=is_enabled(request.app))


@router.put("/maintenance")
async def set_maintenance(
    request: Request,
    state: AdminMaintenanceState,
) -> AdminMaintenanceState:
    """Set the global maintenance flag.

    While enabled, every ``/api`` request from a non-admin fails with
    503 (see ``hivegent.server.maintenance``).  Persists the
    ``ApplicationSettings`` row first, then updates the in-memory cache the
    gate reads — a failed write leaves both layers unchanged.
    """
    await write_maintenance_enabled(state.enabled)
    set_enabled(request.app, state.enabled)
    return state


# ─── System-wide resets ───────────────────────────────────────────────


@router.post("/reset/{target}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_reset(target: Literal["workspace", "database", "factory"]) -> None:
    """Wipe the workspace, the database, or both (``factory``).

    ``workspace`` drops every workspace file and document row, chunks
    (text + vector) cascade with the rows, while conversations, memory,
    users, and groups are kept.

    ``database`` drops every user and group in one transaction
    (:func:`delete_all_identities`) and the conversations' folders.
    Cascade chain (see ``backend/src/hivegent/db/models.py``):
    users → memory, conversations (→ messages), documents (→ chunks),
    groups → group documents.  The workspace tree on disk is not touched.

    ``factory`` drops the workspace tree, the conversations' folders, and
    every identity, leaving the deployment in the shape of a fresh checkout.
    Idempotent, re-running on a clean tree is a no-op.
    """
    match target:
        case "workspace":
            await workspace.delete_workspace_root()
            await delete_all_documents()
        case "database":
            await delete_all_identities()
            await reset_conversation_files()
        case "factory":
            await asyncio.gather(
                workspace.delete_workspace_root(), reset_conversation_files()
            )
            await delete_all_identities()


@router.post("/reindex")
async def admin_reindex() -> AdminReindexResponse:
    """Reconcile every casebase: re-derive the document index from disk.

    Also the on-demand path canonicalisation: reconciliation folds each
    workspace to Unicode NFC before ingesting it, so this is what repairs
    files copied into ``data/workspace/`` by hand without a restart.
    """
    reports = (await reconcile_all()).values()
    synced = sum(r.entries_synced for r in reports)
    renamed = sum(r.files_renamed for r in reports)
    return AdminReindexResponse(
        message=_reindexed(len(reports), synced, renamed).current
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
            else Casebase.for_group(identifier, writable=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/data", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_user_data(user_id: str) -> None:
    """Wipe all data owned by a single user (:func:`delete_user_data`).

    The wiped user is never the caller here, so every one of their tabs is told.
    """
    store = _casebase_or_400("user", user_id)
    await delete_user_data(User(id=store.id))


@router.delete("/groups/{group_id}/data", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_group_data(group_id: str) -> None:
    """Wipe all data owned by a single group.

    Removes the group's workspace directory and SQL document rows
    (which cascade to chunks).  Membership rows are kept so
    re-creating the group is a no-op for the IdP-side group
    assignment.
    """
    store = _casebase_or_400("group", group_id)
    await workspace.delete_all(store)
