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

The same hand-drop is the one route by which a path can still enter the system
without passing a canonicalizing boundary, so each store's file names are
folded to Unicode NFC before the ingest copies them into ``stem_path``.
Every other path is canonicalized at its boundary, so after that pass every
stem is NFC by construction.

Reconciliation never deletes workspace files: they are the authoritative
content.  It renames them only to canonicalize a spelling.

Both passes run through :func:`hivegent.workspace.sync_entries_from_disk`,
the same idempotent fold-back primitive a future read-write shell tool will
call after a session: it ingests every entry on disk and drops every SQL row
whose description is gone.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import normalize_unicode, settings
from .db import documents as db_documents
from .entries import (
    description_path_for_stem,
    is_description_file,
    is_projectable_original,
    stem_path_from_reference,
)
from .files import is_blocked_by_other
from .store import Casebase
from .workspace import sync_entries_from_disk
from .workspace.locks import root_lock

__all__ = [
    "ReconcileReport",
    "reconcile_all",
    "reconcile_store",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ReconcileReport:
    """Per-casebase summary of work done during reconciliation."""

    files_renamed: int = 0
    entries_synced: int = 0


def _normalize_disk_paths(workspace: Path) -> int:
    """Rename every non-NFC file and directory under *workspace* to NFC.

    Deepest first, rewriting only the final component, so an ancestor still
    carries the name the walk recorded when its children are renamed and its
    own rename afterwards carries them along.  Collecting the candidates up
    front is therefore safe: every path stays valid until its own turn.

    Returns:
        The number of paths renamed.
    """
    candidates = [
        path
        for path in workspace.rglob("*")
        if normalize_unicode(path.name) != path.name
    ]
    candidates.sort(key=lambda p: len(p.parts), reverse=True)
    renamed = 0

    for path in candidates:
        target = path.with_name(normalize_unicode(path.name))

        if is_blocked_by_other(target, path):
            logger.warning(
                "Skipping NFC rename of %s: %s already exists as another file",
                path,
                target,
            )
            continue

        path.rename(target)
        renamed += 1

    return renamed


def _disk_entry_references(workspace: Path) -> set[str]:
    """Return the description path of every logical entry the ingest must see.

    A stem qualifies through any markdown description on disk, or through a file
    whose description the ingest pass can derive for it.  Stems are what the sync
    resolves anyway, so yielding them (rather than the file that happened to be
    walked) leaves the sync as the only side that decides which sibling is the
    entry's original.
    """
    stems: set[str] = set()

    for dir_path, _dir_names, file_names in workspace.walk():
        for name in file_names:
            rel = str((dir_path / name).relative_to(workspace).as_posix())

            if is_description_file(rel) or is_projectable_original(rel):
                stems.add(stem_path_from_reference(rel))

    return {description_path_for_stem(stem) for stem in stems}


async def reconcile_store(store: Casebase) -> ReconcileReport:
    """Bring *store*'s SQL index back into agreement with its disk content.

    The filesystem is the source of truth: file names are canonicalized
    first, then every entry on disk or in SQL is synced, which ingests the
    former and drops a row whose description vanished.  One query reads every
    row's state, which the sync compares each entry against.  Chunks cascade with
    documents and need no separate sweep, and workspace files are never
    deleted here.
    """
    workspace = store.workspace_path(settings.data_dir)

    async with root_lock(store):
        renamed = await asyncio.to_thread(_normalize_disk_paths, workspace)

    references = await asyncio.to_thread(_disk_entry_references, workspace)
    states = await db_documents.get_entry_states(store)
    synced = await sync_entries_from_disk(
        store, sorted(references | states.keys()), states
    )

    return ReconcileReport(files_renamed=renamed, entries_synced=synced)


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
            stores.add(Casebase.from_store_key(child.name, writable=True))
        except ValueError:
            logger.warning("Ignoring unrecognised workspace directory: %s", child.name)
    return stores


async def reconcile_all() -> Mapping[str, ReconcileReport]:
    """Reconcile every casebase known to SQL or present on disk.

    A store may exist on only one side: rows survive a workspace that was
    removed, and a hand-created directory has no rows yet, so the sweep has to
    consider both.  Each store holds its own lock, so they run concurrently,
    and a store that raises is logged and dropped from the result rather than
    aborting the rest: one unreadable workspace must not stop the others from
    being repaired.
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
