"""Startup reconciliation between workspace files and SQL.

The filesystem is the source of truth for content.  On boot this module
folds every on-disk markdown description into SQL (new entries are
ingested, drifted entries are re-indexed, unchanged entries are skipped)
and drops SQL rows whose description file is missing on disk.  Chunks
live in the same Postgres database as documents and cascade-delete with
them, so no separate index sweep is needed.

Every path enters through a boundary that canonicalizes it, so a file
dropped into a workspace by hand without a description stays inert content
until it is uploaded.

Reconciliation never deletes or renames workspace files: they are the
authoritative content.  One kind of file
is no content and goes: a file in a source's mirror that none of the source's
items names, since the mirror is derived from the source and its sync would
never delete it, so it is downloaded again if the source still has it.  The
partial files of interrupted writes are swept for the whole data directory
by :func:`hivegent.workspace.sweep_partials`.

Both passes run through :func:`hivegent.workspace.sync_entries_from_disk`,
the same idempotent fold-back primitive a future read-write shell tool will
call after a session: it ingests every entry on disk and drops every SQL row
whose description is gone.
"""

import asyncio
import logging
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path, PurePosixPath

from exar.tools.common import DEFAULT_EXCLUDE_DIRS

from .db import documents as db_documents
from .db import sources as db_sources
from .db.workspaces import list_workspace_keys
from .shared.l10n import Localized
from .store import HostGrant, SourceScope, Workspace, parse_scope
from .tools.changes import ChangeRefused
from .tools.entries import (
    Part,
    is_assets_dir,
    is_description_file,
    stem_path_from_reference,
)
from .workspace import Root, kept_changes, sync_entries_from_disk
from .workspace.locks import root_lock
from .workspace_events import announce_workspace_changed

__all__ = [
    "reconcile_all",
    "reconcile_store",
]

logger = logging.getLogger(__name__)

_KEPT = Localized(
    en="Reindexing is refused while a workspace change whose rollback could not "
    "finish is kept, since its parked files would read as deleted. Restart the "
    "server once the paths its log names are free, so the boot finishes it.",
    de="Die Neuindizierung ist gesperrt, solange eine Änderung im Arbeitsbereich "
    "aufbewahrt wird, deren Rücknahme nicht abgeschlossen werden konnte, da ihre "
    "zurückgestellten Dateien als gelöscht gälten. Starten Sie den Server neu, "
    "sobald die im Protokoll genannten Pfade frei sind, damit er sie abschließt.",
)


async def _refuse_while_kept() -> None:
    """Refuse a reconciliation while a change is kept, see :func:`hivegent.workspace.kept_changes`.

    Raises:
        ChangeRefused: With a 409 while any change is kept.
    """
    if kept := await asyncio.to_thread(kept_changes):
        logger.error("Refusing reconciliation, interrupted changes kept: %s", kept)
        raise ChangeRefused(status_code=409, detail=_KEPT.current)


def _raise(error: OSError) -> None:
    raise error


def _walk(workspace: Path) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Walk *workspace* top-down, pruning the build and vendor directories.

    The same names a listing hides by default (``DEFAULT_EXCLUDE_DIRS``), so
    neither a ``.git`` nor a ``node_modules`` dropped into a workspace is
    indexed behind the tools' backs.

    Raises:
        OSError: When *workspace* or a directory below it cannot be listed,
            since a missing, unreadable, or stale root (an unmounted share)
            would otherwise read as empty and drop every row of its store.
    """
    for dir_path, dir_names, file_names in workspace.walk(on_error=_raise):
        dir_names[:] = [name for name in dir_names if name not in DEFAULT_EXCLUDE_DIRS]
        yield dir_path, dir_names, file_names


def _subject_stem(rel: str) -> str:
    """The stem of the subject a file of a workspace belongs to, an asset's being its entry's.

    >>> _subject_stem("docs/a.assets/img/b.png"), _subject_stem("docs/a.csv.cbrkit.json")
    ('docs/a', 'docs/a')
    """
    path = PurePosixPath(rel)
    assets = next(
        (
            folder
            for folder in reversed(path.parents)
            if is_assets_dir(folder.as_posix())
        ),
        None,
    )

    return Part.of((assets or path).as_posix()).stem_path


def _sweep(mirror: Path, mirrored: Collection[str]) -> int:
    """Remove every file of a subject no item of *mirrored* names from the source's *mirror*.

    Returns:
        The number of files removed.
    """
    stems = {Part.of(path).stem_path for path in mirrored}
    removed = 0

    for dir_path, _dir_names, file_names in _walk(mirror):
        for name in file_names:
            path = dir_path / name

            if _subject_stem(path.relative_to(mirror).as_posix()) not in stems:
                logger.warning("Removing %s, which no entry owns", path)
                path.unlink(missing_ok=True)
                removed += 1

    return removed


def _references(workspace: Path, mirrored: Collection[str] | None = None) -> set[str]:
    """Sweep a source's *mirror* of what no item names, then return the description of every entry under *workspace*.

    Blocking.
    """
    if mirrored is not None:
        _ = _sweep(workspace, mirrored)

    return {
        Part(stem_path_from_reference(rel), "description").path
        for dir_path, _dir_names, file_names in _walk(workspace)
        for name in file_names
        if is_description_file(
            rel := (dir_path / name).relative_to(workspace).as_posix()
        )
    }


async def reconcile_store(store: Workspace) -> int:
    """Bring *store*'s SQL index back into agreement with its disk content.

    The filesystem is the source of truth: in a source's mirror, the files
    no item names are removed, under the lock every commit
    of the sync holds, so the items read are those of the files on disk.  Then
    every entry on disk or in SQL is synced, which ingests the former and drops a row whose description
    vanished.  One query reads every row's state, which the sync compares each
    entry against.  Chunks cascade with documents and need no separate sweep.

    Returns:
        The number of entries whose index changed.

    Raises:
        OSError: When the store's root is missing or a directory of it cannot
            be listed, so no row is dropped for content that is only out of reach.
        ChangeRefused: While a change is kept, see :func:`_refuse_while_kept`.
    """
    await _refuse_while_kept()
    workspace = store.path

    async with root_lock(Root.of(store)):
        mirrored = (
            [item.path for item in await db_sources.load_items(store.scope.id)]
            if isinstance(store.scope, SourceScope)
            else None
        )
        references = await asyncio.to_thread(_references, workspace, mirrored)

    states = await db_documents.get_entry_states(store)
    synced = await sync_entries_from_disk(
        store, sorted(references | states.keys()), states
    )

    if synced:
        announce_workspace_changed((store,))

    return synced


def _disk_store_keys() -> set[str]:
    """Return the store keys of every existing workspace directory."""
    root = Workspace.workspace_root()

    if not root.is_dir():
        return set()

    return {child.name for child in root.iterdir() if child.is_dir()}


async def _known_stores(host: HostGrant) -> list[Workspace]:
    """Every workspace known to SQL or present on disk, granted for writing by the *host*."""
    stores: list[Workspace] = []

    for key in sorted(await list_workspace_keys() | _disk_store_keys()):
        try:
            stores.append(host.workspace(parse_scope(key)))
        except ValueError:
            logger.warning("Ignoring unrecognised workspace directory: %s", key)

    return stores


async def reconcile_all(host: HostGrant) -> Mapping[str, int]:
    """Reconcile every workspace known to SQL or present on disk, as the *host*.

    A store may exist on only one side: rows survive a workspace that was
    removed, and a hand-created directory has no rows yet, so the sweep has to
    consider both.  Each store holds its own lock, so they run concurrently,
    and a store that raises is logged and dropped from the result rather than
    aborting the rest: one unreadable workspace must not stop the others from
    being repaired, and its rows stay until it can be read again.

    Raises:
        ChangeRefused: While a change is kept, see :func:`_refuse_while_kept`.
    """
    await _refuse_while_kept()
    stores = await _known_stores(host)

    async def _reconcile_safe(store: Workspace) -> int | None:
        try:
            return await reconcile_store(store)
        except Exception:
            logger.exception("Reconciliation failed for %s", store.store_key)
            return None

    reports = await asyncio.gather(*(_reconcile_safe(store) for store in stores))
    return {
        store.store_key: report
        for store, report in zip(stores, reports, strict=True)
        if report is not None
    }
