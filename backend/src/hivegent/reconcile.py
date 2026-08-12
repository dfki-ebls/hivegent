"""Startup reconciliation between workspace files and SQL.

The filesystem is the source of truth for content.  On boot this module
folds every on-disk markdown description into SQL — new entries are
ingested, drifted entries are re-indexed, unchanged entries are skipped —
and drops SQL rows whose description file is missing on disk.  Chunks
live in the same Postgres database as documents and cascade-delete with
them, so no separate index sweep is needed.

A hand-dropped file that carries no description of its own is picked up too
when its projection is a verbatim copy of its text (see
:func:`hivegent.entries.is_projectable_original`), so a config or source file
dropped into the workspace becomes a real entry rather than invisible content.
Anything needing a converter or a vision model stays inert until it is uploaded
or reconverted explicitly: neither belongs in a sweep that blocks the server
from accepting traffic.

Reconciliation never deletes workspace files: they are the authoritative
content.

The ingest pass runs through :func:`hivegent.workspace.sync_entries_from_disk`,
the same idempotent fold-back primitive a future read-write shell tool will
call after a session.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from .chunks import delete_documents as _delete_chunked_documents
from .config import settings
from .db import documents as db_documents
from .entries import (
    description_path_for_stem,
    is_description_file,
    is_projectable_original,
    stem_path_from_reference,
)
from .store import Casebase
from .workspace import sync_entries_from_disk
from .workspace.locks import store_lock

__all__ = [
    "ReconcileReport",
    "reconcile_all",
    "reconcile_store",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ReconcileReport:
    """Per-casebase summary of work done during reconciliation."""

    entries_ingested: int = 0
    sql_orphans_removed: int = 0


async def _sweep_sql_orphans(store: Casebase, sql_paths: Mapping[str, int]) -> int:
    """Drop SQL documents whose description file is missing on disk."""
    workspace = store.workspace_path(settings.data_dir)
    missing = [path for path in sql_paths if not (workspace / path).exists()]
    if not missing:
        return 0
    try:
        removed = await _delete_chunked_documents(store, missing)
    except Exception:
        logger.warning(
            "Failed to remove SQL orphans for %s", store.store_key, exc_info=True
        )
        return 0
    return removed


def _disk_entry_references(store: Casebase) -> list[str]:
    """Return the description path of every logical entry the ingest must see.

    A stem qualifies through any markdown description on disk, or through a file
    whose description the ingest pass can derive for it.  Stems are what the sync
    resolves anyway, so yielding them (rather than the file that happened to be
    walked) leaves the sync as the only side that decides which sibling is the
    entry's original.
    """
    workspace = store.workspace_path(settings.data_dir)
    if not workspace.exists():
        return []
    stems: set[str] = set()
    for file_path in workspace.rglob("*"):
        if not file_path.is_file():
            continue
        rel = str(file_path.relative_to(workspace).as_posix())
        if is_description_file(rel) or is_projectable_original(rel):
            stems.add(stem_path_from_reference(rel))
    return [description_path_for_stem(stem) for stem in sorted(stems)]


async def reconcile_store(store: Casebase) -> ReconcileReport:
    """Bring *store*'s SQL index back into agreement with its disk content.

    The filesystem is the source of truth: every on-disk description is
    folded into SQL first, then SQL rows whose description vanished are
    dropped.  Chunks cascade with documents and need no separate sweep, and
    workspace files are never deleted here.
    """
    ingested = await sync_entries_from_disk(store, _disk_entry_references(store))
    async with store_lock(store):
        sql_paths = await db_documents.list_document_paths(store)
        sql_removed = await _sweep_sql_orphans(store, sql_paths)

    return ReconcileReport(
        entries_ingested=ingested,
        sql_orphans_removed=sql_removed,
    )


def _disk_known_stores() -> set[Casebase]:
    """Return casebases with an existing workspace directory."""
    root = Casebase.workspace_root(settings.data_dir)
    if not root.is_dir():
        return set()
    stores: set[Casebase] = set()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            stores.add(Casebase.from_store_key(child.name))
        except ValueError:
            logger.warning("Ignoring unrecognised workspace directory: %s", child.name)
    return stores


async def reconcile_all() -> Mapping[str, ReconcileReport]:
    """Reconcile every casebase known to SQL or present on disk.

    Each store holds its own lock, so reconciliation runs concurrently.
    """
    sql_stores = await db_documents.list_known_stores()
    stores = sorted(sql_stores | _disk_known_stores(), key=lambda s: s.store_key)

    async def _reconcile_safe(store: Casebase) -> ReconcileReport | None:
        try:
            return await reconcile_store(store)
        except Exception:
            logger.warning(
                "Reconciliation failed for %s", store.store_key, exc_info=True
            )
            return None

    reports = await asyncio.gather(*(_reconcile_safe(store) for store in stores))
    return {
        store.store_key: report
        for store, report in zip(stores, reports, strict=True)
        if report is not None
    }
