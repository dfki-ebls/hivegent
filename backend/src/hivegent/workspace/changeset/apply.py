"""Applying a changeset, committing it all or nothing.

:func:`apply_changeset` commits a changeset all or nothing:

1. Resolve every item and convert the uploads and the text originals whose
   markdown projection has to be regenerated, concurrently and without any
   lock, which is skipped when no item uploads or writes a workspace original.
2. Take the locks of every root involved, in ``store_key`` order, so the tasks
   writing one folder (a tool, a program, a spilled result) check and write it
   one at a time.
3. Resolve again, so every text item is derived and every basis, path, filter,
   and quota checked under the locks, and refuse with a 409 when what an item
   converted in step 1 replaces changed meanwhile.
4. Install every file change through one rename journal in a worker thread,
   staged beside every root it changes so each step stays a rename, and apply
   every row change in one SQL transaction, so a failure in either restores
   the files and rolls the transaction back.  Deleted and moved units, and the
   files a quota evicts, leave first, deepest first, a moved one parked in the
   staging area with its rows on a temporary stem.  The moved ones then land,
   shallowest first, and the writes and new directories follow.  Rows are only
   ever updated, never recreated, so a moved document keeps its id and its
   embeddings, and the temporary stem (:data:`_PARK_STEM`) exists only inside
   that transaction.  The journal is logged to disk ahead of every step, so
   the boot rolls back a commit a crash interrupted, and a rollback that
   failed keeps the staging area for the boot to finish.
5. Chunk and index what was written, one entry after another's assets but
   the entries concurrently, still under the locks, so no other change sees an
   entry without its chunks.
6. Release the locks, remove the staging area with the journal, and announce
   the commit once to every client following a workspace it changed, with
   those workspaces and the paths it moved and deleted.

It returns one report per item next to a
:class:`~hivegent.tools.changes.WorkspaceChanged` with those workspaces and paths,
read off the resolved items under the locks, so the caller and the
announcement hold the same record.  A failure in
the last step leaves the new files in place without their chunks, which a null
``content_digest`` marks for the startup reconcile, and raises :class:`IndexingFailed`.
A caller's own row write (``record``) joins the transaction of step 4, whose id
the journal names before it commits (:mod:`hivegent.db.changes`), so the files
follow the rows whether a crash or a lost acknowledgement beat the journal's commit.
"""

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from exar.helpers import bounded_gather, shield_to_completion
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import changes as db_changes
from ...db import documents as db_documents
from ...db import engine as db_engine
from ...metadata import EntryMetadata
from ...shared.tree import tree
from ...store import Workspace
from ...tools.changes import ChangeRefused, Edit, WorkspaceChanged, Write
from ...tools.entries import (
    ContentStat,
    EntryPaths,
    Part,
    repoint_asset_refs,
    stem_path_from_reference,
)
from ...workspace_events import announce_workspace_changed
from ..documents import _decode_existing
from ..indexing import chunk_and_index_document
from ..locks import _locked
from ..metadata import current_entry_metadata
from ..operations import Item, Root, Upload, locations
from ..paths import (
    _STAGE_PREFIX,
    _Journal,
    _shown,
    _WorkspaceChange,
    _write_markdown_file,
    _write_workspace_file,
)
from ..prepare import PreparedUpload, prepare_upload
from .model import (
    _depth,
    _MakeDir,
    _Resolved,
    _TextEffect,
    _Unit,
    _UploadEffect,
    _Vacate,
    _vacate_of,
)
from .resolve import _resolve_all
from .rules import _changed_meanwhile

__all__ = [
    "AppliedChangeset",
    "IndexingFailed",
    "RowChange",
    "apply_changeset",
    "logger",
]

logger = logging.getLogger(__name__)

_PARK_STEM = ".changeset-park"
"""The stem a moved unit's rows wait on between leaving and landing.

Never on disk: the rows pass through it inside the one transaction that moves
them, so no listing, index, or reconcile can meet it.
"""


@dataclass(slots=True, frozen=True)
class AppliedChangeset:
    """What the gateway committed.

    Attributes:
        reports: One human-readable report per operation, in order, for the
            tool or person that asked.
        changed: The workspaces it changed and the paths it moved and
            deleted, as resolved, the record the commit announces.
    """

    reports: tuple[str, ...]
    changed: WorkspaceChanged


type _Converting = _TextEffect | _UploadEffect
"""An item whose projection is converted ahead of the locks."""


@dataclass(slots=True, frozen=True)
class _Converted:
    """An item's projection, converted ahead of the locks from *effect*."""

    store: Workspace
    effect: _Converting
    prepared: PreparedUpload
    original: tuple[str, bytes] | None
    """The original written beside the projection, if any."""
    replacing: bool
    """Whether it replaces an entry, whose stale asset rows then go."""
    modified_at: datetime | None = None
    """When the content last changed, now when unknown."""

    @property
    def stem_path(self) -> str:
        """The stem of the entry it lands."""
        return stem_path_from_reference(self.effect.target.path)


@dataclass(slots=True, frozen=True)
class _Indexing:
    """A projection on disk awaiting its chunks, with the stat it was written at."""

    store: Workspace
    description_path: str
    markdown: str
    stat: ContentStat | None
    entry_metadata: EntryMetadata | None = None
    """``None`` for a text write, whose row, else its files, supply it at index time."""


async def _convert(item: tuple[Workspace, _Converting]) -> _Converted:
    """Run an upload's bytes, or a text original's new ones, through the upload conversion.

    A text original goes through the same pipeline as an upload of the edited
    file, so the projection left behind is byte for byte the one uploading it
    would produce, and a failed conversion leaves the previous entry untouched.
    """
    store, effect = item

    if isinstance(effect, _UploadEffect):
        upload, original = effect.upload, effect.original
        prepared = await prepare_upload(
            store,
            effect.target.path,
            upload.content,
            origin=effect.origin,
            original_path=None if original is None else original[0],
            ctx=upload.progress,
        )

        return _Converted(
            store,
            effect,
            prepared,
            original,
            effect.replacing,
            upload.modified_at,
        )

    path, data = effect.target.path, effect.data
    # A carried original keeps the rows it has until the move lands them here.
    origin = effect.origin
    metadata = (
        await db_documents.get_entry_metadata(holder, origin.path)
        if origin is not None and (holder := origin.root.store) is not None
        else None
    )
    prepared = await prepare_upload(
        store,
        path,
        data,
        origin=metadata.origin if metadata else "imported",
        original_path=path,
        ctx=None,
    )

    return _Converted(
        store,
        effect,
        prepared,
        (path, data),
        effect.current is not None,
    )


type RowChange = Callable[[AsyncSession], Awaitable[object]]
"""One row write, run inside the changeset's single transaction."""


class IndexingFailed(Exception):
    """A changeset committed, but chunking or indexing what it wrote failed.

    The files and rows stand, and a null ``content_digest`` marks what the
    startup reconcile indexes, so a caller must not retry the changeset.
    """

    def __init__(self, applied: AppliedChangeset) -> None:
        super().__init__("Indexing a committed changeset failed")
        self.applied = applied


def _stage_converted(staging: Path, converted: _Converted) -> list[_WorkspaceChange]:
    """Write a converted entry's files under *staging*, returning the swap to make.

    Nothing live is touched: the returned changes name each workspace path the
    entry replaces, its description and ``.assets`` directory always among them,
    by the new one or by nothing, so a reconversion never keeps a stale asset
    and an original the model could not describe keeps no stale description.
    """
    prepared, stem = converted.prepared, converted.stem_path
    description, assets_dir = Part(stem, "description").path, Part(stem, "assets").path
    changes = [
        _WorkspaceChange(
            description, None if prepared.main is None else staging / description
        )
    ]

    for asset in prepared.assets:
        _write_workspace_file(staging, asset.path, asset.data, sync_folder=False)

    for entry in prepared.entries:
        _write_markdown_file(
            staging, entry.description_path, entry.markdown, sync_folder=False
        )

    has_assets = bool(prepared.assets or prepared.asset_entries)
    changes.append(
        _WorkspaceChange(assets_dir, staging / assets_dir if has_assets else None)
    )

    if converted.original is not None:
        path, data = converted.original
        changes.append(
            _WorkspaceChange(
                path, _write_workspace_file(staging, path, data, sync_folder=False)
            )
        )

    if (modified_at := converted.modified_at) is not None:
        for change in changes:
            if change.staged_path is not None:
                _date_tree(change.staged_path, modified_at)

    return changes


def _date_tree(path: Path, modified_at: datetime) -> None:
    """Date *path* and everything below it at *modified_at*."""
    timestamp = modified_at.timestamp()

    for entry in tree(path):
        os.utime(entry, (timestamp, timestamp))


def _install_converted(
    converted: _Converted, journal: _Journal, staging: Path
) -> list[_Indexing]:
    """Install a converted entry's files, returning its projections in index order.

    Assets come first and the entry last, so the markdown that references the
    assets is only indexed once their own entries are.
    """
    workspace = converted.store.path

    for change in _stage_converted(staging, converted):
        journal.apply(workspace, change)

    prepared = converted.prepared

    return [
        _Indexing(
            converted.store,
            entry.description_path,
            entry.markdown,
            ContentStat.from_path(workspace / entry.description_path),
            entry.entry_metadata,
        )
        for entry in prepared.entries
    ]


def _install_text(
    effect: _TextEffect, journal: _Journal, staging: Path
) -> list[_Indexing]:
    """Install a text item's file as it is, returning its projection to index.

    A plain file is indexed nowhere.
    """
    target, store = effect.target, effect.store
    live = target.full_path
    journal.install(
        _write_workspace_file(staging, target.path, effect.data),
        live,
    )

    if store is None or not effect.indexed:
        return []

    stat = ContentStat.from_path(live)

    return [_Indexing(store, target.path, effect.content, stat)]


def _converted_rows(converted: _Converted) -> list[RowChange]:
    """Drop the asset rows of a replaced entry, whose new projection supersedes them.

    Its own row goes too when the new original has no description to keep it.
    """
    if not converted.replacing:
        return []

    store, stem = converted.store, converted.stem_path

    if converted.prepared.main is None:
        entry = EntryPaths(stem, frozenset({"description", "assets"}))

        return [lambda s: _remove_entry_rows(s, store, entry)]

    assets = Part(stem, "assets").path

    return [lambda s: db_documents.delete_subtree(store, assets, s=s)]


def _move_rows(source: _Unit, destination: _Unit) -> list[RowChange]:
    """Re-key a unit's rows, which keeps their ids, and so their chunks.

    A plain file has none.
    """
    origin, target = source.root.store, destination.root.store

    if origin is None or target is None:
        return []

    if source.entry is None:
        return [
            lambda s: db_documents.move_subtree(
                origin, source.path, target, destination.path, s=s
            )
        ]

    changes: list[RowChange] = [
        lambda s: db_documents.move_document(
            origin, source.path, target, destination.path, s=s
        )
    ]
    old, new = source.entry.assets_dir, destination.paths.path("assets")

    if old is not None:
        changes.append(
            lambda s: db_documents.move_subtree(origin, old, target, new, s=s)
        )

    return changes


def _remove_entry_files(journal: _Journal, workspace: Path, entry: EntryPaths) -> None:
    """Park an entry's description, original, and assets in *journal*."""
    for path in entry.files:
        journal.remove(workspace / path)


async def _remove_entry_rows(
    s: AsyncSession, store: Workspace, entry: EntryPaths
) -> None:
    """Drop an entry's row and its asset rows inside the caller's transaction."""
    await db_documents.delete_document(store, entry.description_path, s=s)

    if entry.assets_dir:
        await db_documents.delete_subtree(store, entry.assets_dir, s=s)


type _Park = tuple[Path, _Unit]
"""Where a moved unit waits between leaving and landing: its files' directory, its rows' stem."""


def _leave(effect: _Vacate, journal: _Journal, park: _Park) -> list[RowChange]:
    """Delete a unit, or park its files in a staging directory and its rows on a stem."""
    unit = effect.unit

    if effect.destination is None:
        return _remove(unit, journal)

    parked, stem = park
    workspace = unit.root.path

    for path in unit.files:
        if os.path.lexists(workspace / path):
            journal.rename(workspace / path, parked / PurePosixPath(path).name)

    return _move_rows(unit, stem)


def _remove(unit: _Unit, journal: _Journal) -> list[RowChange]:
    """Park a deleted unit's files for good, returning its rows to drop.

    A plain file has no rows, an entry its own, and a tree all those below its path.
    """
    path, owner, entry = unit.root.path, unit.root.store, unit.entry

    if entry is None:
        for file in unit.files:
            journal.remove(path / file)
    else:
        _remove_entry_files(journal, path, entry)

    if owner is None:
        return []

    if entry is not None:
        return [lambda s: _remove_entry_rows(s, owner, entry)]

    return [lambda s: db_documents.delete_subtree(owner, unit.path, s=s)]


def _land(
    unit: _Unit, destination: _Unit, journal: _Journal, park: _Park, staging: Path
) -> tuple[list[RowChange], list[_Indexing]]:
    """Move a parked unit's files and rows to *destination*, returning the projection to reindex.

    The description addresses its payload as ``<stem>.assets/...``, so its
    references follow a changed basename, and its chunks with them.
    """
    parked, stem = park
    workspace = destination.root.path
    names = PurePosixPath(unit.path).name, PurePosixPath(destination.path).name
    entry = unit.entry
    description = entry.description_path if entry and entry.assets_dir else None
    reindex: list[_Indexing] = []

    for old, new in zip(unit.files, destination.files, strict=True):
        held = parked / PurePosixPath(old).name

        if not os.path.lexists(held):
            continue

        if old == description and names[0] != names[1]:
            text = _decode_existing(held, _shown(unit.root, old)).text
            repointed = repoint_asset_refs(text, *names)
            staged = _write_workspace_file(staging, new, repointed.encode("utf-8"))
            # Repointed references leave the content as it was, so its date stays.
            shutil.copystat(held, staged)
            journal.install(staged, workspace / new)

            if (store := destination.root.store) is not None:
                reindex.append(
                    _Indexing(
                        store, new, repointed, ContentStat.from_path(workspace / new)
                    )
                )
        else:
            journal.rename(held, workspace / new)

    return _move_rows(stem, destination), reindex


def _install_files(
    resolved: Sequence[_Resolved],
    evicted: Iterable[_Unit],
    converted: Mapping[int, _Converted],
    staging: Path,
    journal: _Journal,
) -> tuple[list[list[_Indexing]], list[RowChange]]:
    """Land every item's files, returning the projections to index and the row changes to make.

    Blocking, and the caller's journal restores the files on failure.  The
    files a quota evicts leave with the deletes.
    """
    journal.begin()
    vacates = (
        *(
            (index, vacate)
            for index, item in enumerate(resolved)
            if (vacate := _vacate_of(item.effect)) is not None
        ),
        *((len(resolved) + n, _Vacate(unit, None)) for n, unit in enumerate(evicted)),
    )
    vacating = sorted(vacates, key=lambda pair: _depth(pair[1].unit.path), reverse=True)
    moving = sorted(
        (
            (index, effect.unit, destination)
            for index, effect in vacating
            if (destination := effect.destination) is not None
        ),
        key=lambda move: _depth(move[2].path),
    )
    # Rows wait on a stem no document has, so a swap never meets itself.
    token = f"{_PARK_STEM}/{uuid4().hex}"
    parks: dict[int, _Park] = {}
    rows: list[RowChange] = []
    pending: list[list[_Indexing]] = []

    for index, effect in vacating:
        unit = effect.unit
        parks[index] = (
            staging / "park" / str(index),
            unit.at(unit.root, f"{token}/{index}"),
        )
        rows += _leave(effect, journal, parks[index])

    for index, unit, destination in moving:
        landed, reindex = _land(
            unit, destination, journal, parks[index], staging / str(index)
        )
        rows += landed

        if reindex:
            pending.append(reindex)

    for index, item in enumerate(resolved):
        match item.effect:
            case _TextEffect() | _UploadEffect() if index in converted:
                pending.append(
                    _install_converted(converted[index], journal, staging / str(index))
                )
                rows += _converted_rows(converted[index])
            case _TextEffect() as effect:
                pending.append(_install_text(effect, journal, staging / str(index)))
            case _MakeDir(target=target):
                journal.mkdir(target.full_path)
            case _Vacate():
                pass

    return pending, rows


def _staging_root(roots: Iterable[Root]) -> Path:
    """Where a commit stages and parks, beside every root it changes so each step stays a rename.

    Named afresh per commit, and created by the first step that stages into it.
    """
    parent = os.path.commonpath([root.path.parent for root in roots])

    return Path(parent, f"{_STAGE_PREFIX}{uuid4().hex}")


async def _index_entry(projections: Sequence[_Indexing]) -> Exception | None:
    """Chunk and index one entry's projections in order, its assets before itself.

    Returns the first failure rather than raising it, so the other entries
    indexing alongside are not cancelled.
    """
    failure: Exception | None = None

    for item in projections:
        try:
            metadata = item.entry_metadata or await current_entry_metadata(
                item.store, item.description_path
            )
            await chunk_and_index_document(
                item.store,
                item.description_path,
                item.markdown,
                stat=item.stat,
                entry_metadata=metadata,
            )
        except Exception as exc:
            logger.warning(
                "Indexing %s/%s failed",
                item.store.store_key,
                item.description_path,
                exc_info=True,
            )
            failure = failure or exc

    return failure


async def _index_all(pending: Sequence[Sequence[_Indexing]]) -> Exception | None:
    """Chunk and index every written entry concurrently, returning the first failure.

    One failure does not strand the rest without chunks, since every projection has its turn.
    """
    failures = await bounded_gather(
        pending, _index_entry, limit=settings.jobs.collection_concurrency
    )

    return next((failure for failure in failures if failure is not None), None)


async def _rows_committed(journal: _Journal) -> bool | None:
    """Whether the rows of a change that failed committed nonetheless, as when only the acknowledgement was lost.

    None when PostgreSQL cannot tell, since the rows may have committed all the same.
    """
    if journal.xid is None:
        return False

    try:
        return await db_changes.committed(journal.xid)
    except Exception:
        logger.warning(
            "Cannot ask whether transaction %s committed", journal.xid, exc_info=True
        )

        return None


async def _commit(
    items: Sequence[Item],
    converted: Mapping[int, _Converted],
    host: bool,
    record: RowChange | None,
) -> AppliedChangeset:
    roots = [location.root for location in locations(items)]
    journal = _Journal(_staging_root(roots))

    try:
        async with _locked(*roots):
            resolved, plan, changed = await _resolve_all(items, host=host)

            for index, done in converted.items():
                if resolved[index].effect != done.effect:
                    shown = done.effect.target.canonical
                    raise ChangeRefused(
                        status_code=409, detail=_changed_meanwhile(shown).current
                    )

            try:
                pending, rows = await asyncio.to_thread(
                    _install_files,
                    resolved,
                    plan.evicted,
                    converted,
                    journal.staging / "new",
                    journal,
                )
                if record is not None:
                    rows.append(record)

                if rows:
                    async with db_engine.session() as s:
                        for change in rows:
                            await change(s)

                        # Named before the commit, so the files can follow the rows whatever cuts it short.
                        await asyncio.to_thread(
                            journal.prepare, await db_changes.transaction_id(s)
                        )

            except BaseException:
                match await _rows_committed(journal):
                    case True:
                        await asyncio.to_thread(journal.commit)
                    case False:
                        await asyncio.to_thread(journal.rollback)
                    case None:
                        # Kept as it is, for the next boot to settle once PostgreSQL answers.
                        logger.error(
                            "Cannot tell whether the change staged in %s committed",
                            journal.staging,
                        )

                raise

            await asyncio.to_thread(journal.commit)

            failure = await _index_all(pending)

    finally:
        _ = await asyncio.to_thread(journal.close)

    applied = AppliedChangeset(tuple(item.report for item in resolved), changed)
    announce_workspace_changed(
        {store for root in roots if (store := root.store) is not None}, changed
    )

    if failure is not None:
        raise IndexingFailed(applied) from failure

    return applied


async def apply_changeset(
    items: Sequence[Item],
    *,
    host: bool = False,
    record: RowChange | None = None,
) -> AppliedChangeset:
    """Commit *items* all or nothing, see the module docstring for the phases.

    Args:
        items: The operations to apply, planned or as a caller spelled them,
            and the uploads among them.
        host: Whether the host commits, which may change what a root reserves
            and evict what its quota lets go to fit.
        record: A row write of the caller's own in the changeset's transaction,
            which commits with the files or not at all, and whose exception
            rolls the files back.

    Returns:
        The reports and how the workspaces changed.

    Raises:
        ChangeRefused: When the changeset is invalid (see
            :func:`plan_changeset`), or what an upload or a text original
            replaces changed while it was being converted.
        IndexingFailed: When the changeset committed but indexing what it wrote failed.
    """
    if not items:
        if record is not None:
            async with db_engine.session() as s:
                await record(s)

        return AppliedChangeset((), WorkspaceChanged(()))

    converted: dict[int, _Converted] = {}

    if any(_converts(op) for op in items):
        planned, _plan, _changed = await _resolve_all(items, host=host)
        converting = {
            index: (store, effect)
            for index, item in enumerate(planned)
            if isinstance(effect := item.effect, _TextEffect | _UploadEffect)
            and (isinstance(effect, _UploadEffect) or effect.is_original)
            and (store := effect.target.root.store) is not None
        }
        done = await bounded_gather(
            converting.values(), _convert, limit=settings.jobs.collection_concurrency
        )
        converted = dict(zip(converting, done, strict=True))

    # Shielded as one unit, so a cancel can never settle between the file swap
    # and the index and leave new markdown wearing its predecessor's rows.
    return await shield_to_completion(_commit(items, converted, host, record))


def _converts(op: Item) -> bool:
    """Whether *op* converts ahead of the locks: an upload, or text written to a workspace original."""
    return isinstance(op, Upload) or (
        isinstance(op, Write | Edit)
        and op.target.root.store is not None
        and Part.of(op.target.path).kind == "original"
    )
