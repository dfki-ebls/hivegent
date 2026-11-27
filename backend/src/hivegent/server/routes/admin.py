"""Admin-only routes for the overview, maintenance, reindexing, and what the operator configured.

Every endpoint in this router gates on :func:`hivegent.auth.require_admin`
via the router-level dependency.  Wiping another user's or a group's data, or
the whole deployment's, is no HTTP route at all, it runs on the host through
``hivegent reset``, and no admin acts as another user.
"""

import asyncio
from collections.abc import Callable, Iterable
from typing import Literal

from exar.helpers import get_name
from exar.text import plural
from exar.tools import effect_of
from fastapi import APIRouter, Depends, HTTPException, status

from ...agents.integrations import INTEGRATIONS
from ...auth import require_admin
from ...db import sources as db_sources
from ...db.application_settings import write_maintenance_enabled
from ...db.groups import list_groups_with_counts
from ...db.users import list_users_with_counts
from ...pipelines import PipelineInfo, chunking_pipelines, conversion_pipelines
from ...plugins.loader import LoadedPlugin, plugins
from ...reconcile import reconcile_all
from ...shared.l10n import Localized
from ...sources.catalog import SOURCES
from ...sources.sync import progress, request_sync
from ...store import HostGrant
from ...types import (
    AdminGroupInfo,
    AdminMaintenanceState,
    AdminOverviewResponse,
    AdminReindexResponse,
    AdminUserInfo,
    Integration,
    PluginFeatureInfo,
    PluginInfo,
    SourceStatus,
)
from ..maintenance import is_enabled, set_enabled

__all__ = ["overview", "router"]

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _reindexed(stores: int, synced: int) -> Localized[str]:
    return Localized(
        en=(
            f"Reconciled {stores} {plural(stores, 'workspace')}: "
            f"{synced} {plural(synced, 'entry', 'entries')} synced"
        ),
        de=(
            f"{stores} {plural(stores, 'Arbeitsbereich', 'Arbeitsbereiche')} "
            f"abgeglichen: {synced} {plural(synced, 'Eintrag', 'Einträge')} "
            f"aktualisiert"
        ),
    )


# ─── Overview ─────────────────────────────────────────────────────────


def overview(
    users: Iterable[tuple[str, int, int]], groups: Iterable[tuple[str, int]]
) -> AdminOverviewResponse:
    """The overview of *users* and *groups*, each with its id and the counts of what it holds."""
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


@router.get("/overview")
async def get_overview() -> AdminOverviewResponse:
    """List the users and groups that stored something, with what they hold."""
    return overview(
        *await asyncio.gather(list_users_with_counts(), list_groups_with_counts())
    )


# ─── Maintenance mode ─────────────────────────────────────────────────


@router.get("/maintenance")
async def get_maintenance() -> AdminMaintenanceState:
    """Report the current state of the global maintenance flag."""
    return AdminMaintenanceState(enabled=is_enabled())


@router.put("/maintenance")
async def set_maintenance(state: AdminMaintenanceState) -> AdminMaintenanceState:
    """Set the global maintenance flag.

    While enabled, every request to the API or ``/mcp`` from a non-admin
    fails with 503 (see ``hivegent.server.maintenance``).  Persists the
    ``ApplicationSettings`` row first, then updates the in-memory cache the
    gate reads, so a failed write leaves both layers unchanged.
    """
    await write_maintenance_enabled(state.enabled)
    set_enabled(state.enabled)
    return state


# ─── Integrations ───────────────────────────────────────────────────


@router.get("/integrations")
async def list_integrations() -> list[Integration]:
    """List the configured MCP servers, databases, and APIs with the tools each offers."""
    return [integration.integration() for integration in INTEGRATIONS]


# ─── Pipelines ────────────────────────────────────────────────────────

type PipelineStage = Literal["conversion", "chunking"]

_STAGES: dict[PipelineStage, Callable[[], list[PipelineInfo]]] = {
    "conversion": conversion_pipelines,
    "chunking": chunking_pipelines,
}


@router.get("/pipelines/{stage}")
async def list_pipelines(stage: PipelineStage) -> list[PipelineInfo]:
    """The installed pipelines of one processing stage, ``auto`` first, in the request's language."""
    return _STAGES[stage]()


# ─── External sources ─────────────────────────────────────────────────

_UNKNOWN_SOURCE = Localized(en="No such source", de="Diese Quelle gibt es nicht")


@router.get("/sources")
async def list_sources() -> list[SourceStatus]:
    """List the configured sources with how their last sync went."""
    states = await db_sources.load_states(counted=True)

    return [
        SourceStatus(
            id=source.id,
            connector=source.config.connector,
            host=source.config.url.host or "" if source.config.url else "",
            groups=source.config.groups,
            interval_minutes=source.config.interval_minutes,
            requested=state.requested,
            running=running is not None,
            done=0 if running is None else running.done,
            total=0 if running is None else running.total,
            synced_at=state.synced_at,
            error=state.error,
            held_deletes=state.held,
            items=state.items,
        )
        for source in SOURCES.values()
        for state in (states.get(source.id, db_sources.SourceState()),)
        for running in (progress(source.id),)
    ]


@router.post("/sources/{source_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_source_now(source_id: str, confirm_deletes: bool = False) -> None:
    """Request a sync of a source, which runs soon, *confirm_deletes* deleting what its brake held."""
    source = SOURCES.get(source_id)

    if source is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_SOURCE.current)

    await request_sync(source, confirm=confirm_deletes)


# ─── Plugins ──────────────────────────────────────────────────────────


def _plugin_info(loaded: LoadedPlugin) -> PluginInfo:
    plugin = loaded.plugin

    return PluginInfo(
        name=loaded.name,
        features=[
            PluginFeatureInfo(
                id=feature.id,
                tools={get_name(tool): effect_of(tool) for tool in feature.tools},
            )
            for feature in plugin.features
        ],
        converters=sorted(plugin.converters),
        chunkers=sorted(plugin.chunkers),
        skills=sorted(
            folder.name
            for directory in plugin.skills
            for folder in directory.iterdir()
            if folder.is_dir() and not folder.name.startswith(".")
        ),
    )


@router.get("/plugins")
async def list_plugins() -> list[PluginInfo]:
    """List the enabled plugins with what each contributes, tools by their effect."""
    return [_plugin_info(loaded) for loaded in plugins()]


# ─── Index ───────────────────────────────────────────────────────────


@router.post("/reindex")
async def admin_reindex() -> AdminReindexResponse:
    """Reconcile every workspace: re-derive the document index from disk.

    Refused with a 409 while a change whose rollback could not finish is kept.
    """
    synced = (await reconcile_all(HostGrant())).values()
    return AdminReindexResponse(message=_reindexed(len(synced), sum(synced)).current)
