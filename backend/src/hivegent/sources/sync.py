"""Mirroring each source to disk, on its interval and on an admin's request.

A sync asks the connector what changed since the stored cursor, or diffs a
full listing against the stored items, and plans it (:func:`plan_sync`): a
file new or at another version is downloaded, one at another path under the
same id is moved, keeping its document and embeddings, and one the source
reports deleted is deleted at once, as is one the source lost, unless that
looks like a mass deletion and is held for an admin, both by the rules of
:func:`plan_sync`, while the rest of the sync lands and the cursor advances.
A listing the source refused in part (``Delta.refused``)
lands what it listed and the removals the source reports but deletes nothing
else, keeps the cursor, and records the refused
paths as the source's error, as rclone refuses deletions after a listing
error.  Everything lands through the
changeset gateway as the host, the downloads as uploads of origin ``synced``,
so the mirror is converted, chunked, and indexed like any workspace.  The
items are recorded in the transaction of the changeset that lands their
files, so the two never disagree, and the cursor after, and a file that
failed keeps the cursor where it was, so the next sync tries it again and
skips what already landed.

The mirror holds what the items, the files on disk, and the rows agree on.
An item whose file or row went missing behind the sync's back, by a restored
volume or a hand that deleted it, is downloaded again from a full listing,
and the reconcile at boot removes a mirrored file no item names
(:func:`hivegent.reconcile.reconcile_store`).  A remote path is mirrored at its
Unicode NFC spelling, and two that fold onto one are named in the source's error.

The server runs one loop that starts a sync of each source that is
requested or due as a task of its own, one per source, whose progress it
keeps in memory and announces to the admins.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncGenerator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import timedelta
from itertools import batched
from pathlib import Path, PurePosixPath
from typing import NoReturn

from exar.helpers import bounded_gather
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import sanitize_document_path, settings
from ..db import documents as db_documents
from ..db import sources as db_sources
from ..files import remove_path
from ..jobs import ADMINS, SourceChanged, manager
from ..shared.connectors import Delta, RemoteItem
from ..shared.l10n import Localized
from ..shared.tasks import background, cancel_and_drain
from ..shared.text import failure_text, normalize_unicode
from ..store import HostGrant, SourceScope, Workspace
from ..tools.changes import Delete, Move
from ..tools.entries import (
    CASE_BASE_KINDS,
    ContentStat,
    Part,
    is_reserved_path,
    stem_path_from_reference,
)
from ..workspace import IndexingFailed, Location, RowChange, Upload, apply_changeset
from ..workspace.operations import Item, Root
from . import catalog
from .catalog import CLIENTS, MirroredSource

__all__ = [
    "SWEEP_FLOOR",
    "Progress",
    "SyncPlan",
    "SyncReport",
    "plan_sync",
    "progress",
    "prune_sources",
    "request_sync",
    "sources_lifespan",
    "sync_source",
]

logger = logging.getLogger(__name__)

_TICK_SECONDS = 60.0
"""How often the loop looks for a source that is due."""

_ANNOUNCE_SECONDS = 5.0
"""How often a running sync at most tells the admins how far it got, each of whom lists the sources again."""

_wake = asyncio.Event()
"""Set to let the loop look at once, for a request."""

_tasks: dict[str, asyncio.Task[None]] = {}
"""The running syncs by their source."""

_BATCH = 32
"""How many downloads land in one changeset, so a sync announces itself in few commits."""

SWEEP_FLOOR = 10
"""How many missing files a sync deletes whatever the share of the mirror, so a small mirror shrinks."""


_MAX_NAMED = 5
"""How many refused paths the source's error names, the rest counted."""


def _refused(paths: Sequence[str]) -> Localized[str]:
    named = ", ".join(paths[:_MAX_NAMED])
    rest = len(paths) - _MAX_NAMED
    more = (f" and {rest} more", f" und {rest} weiteren") if rest > 0 else ("", "")

    return Localized(
        en=(
            f"The source refused to list {named}{more[0]}, so this sync deleted "
            "no file merely missing from the listing."
        ),
        de=(
            f"Die Quelle verweigerte die Auflistung von {named}{more[1]}, daher "
            "löschte dieser Abgleich keine Datei, die nur in der Auflistung fehlte."
        ),
    )


def _failed(count: int) -> Localized[str]:
    return Localized(
        en=f"{count} files could not be mirrored, the next sync tries them again.",
        de=(
            f"{count} Dateien konnten nicht gespiegelt werden, der nächste "
            "Abgleich versucht sie erneut."
        ),
    )


def _collided(paths: Sequence[str]) -> Localized[str]:
    named = ", ".join(paths[:_MAX_NAMED])
    rest = len(paths) - _MAX_NAMED
    more = (f" and {rest} more", f" und {rest} weiteren") if rest > 0 else ("", "")

    return Localized(
        en=(
            f"The source names {named}{more[0]} in two Unicode spellings, so only "
            "one of each is mirrored."
        ),
        de=(
            f"Die Quelle benennt {named}{more[1]} in zwei Unicode-Schreibweisen, "
            "daher wird jeweils nur eine gespiegelt."
        ),
    )


def _mirrorable(path: str) -> bool:
    """Whether a mirror holds *path*: a safe path, outside any payload, not hidden, and no cbrkit companion.

    The sidecar is left out like cbrkit's cache and lock while the format of
    cbrkit's specifications still evolves, so a mirrored one is lost and deleted.

    >>> _mirrorable("docs/a.pdf"), _mirrorable(".git/config"), _mirrorable("a.assets/b.png")
    (True, False, False)
    >>> _mirrorable("docs/a.csv.cbrkit.json")
    False
    """
    if Part.of(path).kind in CASE_BASE_KINDS:
        return False

    try:
        safe = sanitize_document_path(path) == path
    except ValueError:
        return False

    hidden = any(part.startswith(".") for part in PurePosixPath(path).parts)

    return safe and not hidden and not is_reserved_path(path)


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncPlan:
    """What a sync changes in a mirror.

    Args:
        downloads: The files that are new or at another version.
        moves: The mirrored files at another path now, each with its new state.
        deletes: The mirrored files gone from the source.
        skipped: The paths of the files the mirror does not hold or that collide with another file.
        held: The mirrored files the source lost whose deletion waits for an admin, now and from before.
    """

    downloads: tuple[RemoteItem, ...] = ()
    moves: tuple[tuple[RemoteItem, RemoteItem], ...] = ()
    deletes: tuple[RemoteItem, ...] = ()
    skipped: tuple[str, ...] = ()
    held: tuple[RemoteItem, ...] = ()


def _tombstoned(known: Sequence[RemoteItem], delta: Delta) -> set[str]:
    """The ids of the known files *delta* removes and does not list."""
    listed = {item.id for item in delta.items}
    removed = set(delta.removed)
    removed_paths = {PurePosixPath(token) for token in removed}

    def removes(item: RemoteItem) -> bool:
        path = PurePosixPath(item.path)

        return (
            item.id in removed
            or item.path in removed
            or not removed_paths.isdisjoint((path, *path.parents))
        )

    return {item.id for item in known if item.id not in listed and removes(item)}


def _plan(
    known: Sequence[RemoteItem],
    delta: Delta,
    *,
    mirrorable: Collection[str],
    tombstoned: set[str],
    held: Collection[str],
    spared: Collection[str],
    stale: Collection[str],
) -> SyncPlan:
    """The plan that deletes every known file the source lost, except those *spared*, and downloads the *stale* ones again."""
    by_id = {item.id: item for item in known}
    listed_ids = {item.id for item in delta.items}
    unlisted = by_id.keys() - listed_ids
    absent = unlisted - tombstoned if delta.complete else set[str]()
    doomed = (tombstoned | absent | (unlisted & set(held))) - set(spared)
    listed = {item.id: item for item in delta.items if item.id in mirrorable}
    skipped = [item.path for item in delta.items if item.id not in listed]
    gone = (((listed_ids - listed.keys()) & by_id.keys()) - set(spared)) | doomed
    arriving = {
        item.id: item
        for item in listed.values()
        if item.id not in by_id or by_id[item.id].path != item.path
    }
    leaving = gone | arriving.keys()
    staying = [item for item in known if item.id not in leaving]
    collided: set[str] = set()

    # Stems are claimed in the final state, so a file may take the stem another vacates
    # in the same delta.  A file that stays wins a stem, and a spared file that loses
    # one stays where it was, so it claims its old stem in the next round.
    while True:
        stems = {stem_path_from_reference(item.path): item.id for item in staying}
        losers = [
            item
            for item in arriving.values()
            if item.id not in collided
            and stems.setdefault(stem_path_from_reference(item.path), item.id)
            != item.id
        ]

        if not losers:
            break

        for item in losers:
            collided.add(item.id)

            if (old := by_id.get(item.id)) is not None:
                if old.id in spared:
                    staying.append(old)
                else:
                    gone.add(old.id)

    downloads: list[RemoteItem] = []
    moves: list[tuple[RemoteItem, RemoteItem]] = []

    for item in listed.values():
        old = by_id.get(item.id)

        if item.id in collided:
            skipped.append(item.path)
            continue

        if old is not None and old.path != item.path:
            moves.append((old, item))

        if old is None or old.version != item.version or item.id in stale:
            downloads.append(item)

    return SyncPlan(
        downloads=tuple(downloads),
        moves=tuple(moves),
        deletes=tuple(by_id[id] for id in sorted(gone)),
        skipped=tuple(skipped),
    )


def plan_sync(
    known: Sequence[RemoteItem],
    delta: Delta,
    *,
    held: Collection[str] = (),
    stale: Collection[str] = (),
    max_sweep_share: float = 1.0,
    confirmed: bool = False,
) -> SyncPlan:
    """Plan the sync of a mirror holding *known*, of which *held* are held, to what *delta* reports.

    A *stale* known file, whose copy in the mirror went missing, is downloaded
    again wherever the delta lists it.

    A file at a known path and another version is downloaded, a known file at
    another path is moved and downloaded if its version changed too, and a
    known file the delta removes is deleted at once, as rclone does, never
    held.  The mirror admits the files it can hold.  A file landing on a stem another file keeps is skipped,
    since an entry has one original.

    A known file is lost when a complete listing, one without refused paths
    that lists every file, lacks it, when the delta lists it though the mirror
    no longer admits it, or when it collides with another file at its new stem.
    Lost files are deleted, but they stay held for an admin and mirrored,
    while the rest lands, if the delta lists nothing the
    mirror admits, or they, the held ones included, exceed
    *max_sweep_share* of the mirror and :data:`SWEEP_FLOOR` files, or any is
    held already, since an outage or a share unmounted lists like a mass
    deletion.  A held file the delta lists again is no longer lost.  The
    caller lists in full only when an admin confirms, which deletes what was
    held only from a complete listing and otherwise keeps holding it, while a
    file lost since stays held, since the admin never saw it.
    """
    tombstoned = _tombstoned(known, delta)
    mirrorable = {item.id for item in delta.items if _mirrorable(item.path)}
    plan = _plan(
        known,
        delta,
        mirrorable=mirrorable,
        tombstoned=tombstoned,
        held=held,
        spared=frozenset(),
        stale=stale,
    )
    lost = {item.id for item in plan.deletes} - tombstoned

    if confirmed:
        spared = lost - set(held) if delta.complete else lost
    elif lost and (
        not lost.isdisjoint(held)
        or (delta.complete and not mirrorable)
        or len(lost) > max(max_sweep_share * len(known), SWEEP_FLOOR)
    ):
        spared = lost
    else:
        spared = set[str]()

    if not spared:
        return plan

    # A known file moved onto the stem of a spared one collides, so it is lost too.
    while True:
        plan = _plan(
            known,
            delta,
            mirrorable=mirrorable,
            tombstoned=tombstoned,
            held=held,
            spared=spared,
            stale=stale,
        )
        collided = {item.id for item in plan.deletes} - tombstoned - lost

        if not collided:
            break

        lost |= collided
        spared |= collided

    return replace(plan, held=tuple(item for item in known if item.id in spared))


@dataclass(slots=True)
class SyncReport:
    """What one sync did."""

    downloaded: int = 0
    moved: int = 0
    deleted: int = 0
    held: int = 0
    failed: int = 0
    skipped: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    collided: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Spelled:
    """A delta whose paths are spelled as the mirror holds them, and the remote item behind each.

    Args:
        delta: The delta with every path folded to NFC.
        remote: The item as the source names it, by id, which a read needs.
        collided: The remote paths dropped since another folds onto the same one.
    """

    delta: Delta
    remote: Mapping[str, RemoteItem]
    collided: tuple[str, ...]


def _spell(delta: Delta) -> _Spelled:
    """Fold the paths of *delta* to NFC, keeping one item where several fold onto one path.

    The one already spelled in NFC wins, else the first by remote path, so the choice is stable.

    >>> spelled = _spell(Delta(items=[RemoteItem(id="1", path="U\\u0308.md", version="v")]))
    >>> spelled.delta.items[0].path == "\\u00dc.md", spelled.remote["1"].path == "U\\u0308.md"
    (True, True)
    """
    kept: dict[str, RemoteItem] = {}
    collided: list[str] = []

    for item in sorted(
        delta.items,
        key=lambda item: (normalize_unicode(item.path) != item.path, item.path),
    ):
        path = normalize_unicode(item.path)

        if path in kept:
            collided.append(item.path)
        else:
            kept[path] = item

    return _Spelled(
        replace(
            delta,
            items=[replace(item, path=path) for path, item in kept.items()],
            removed=[normalize_unicode(token) for token in delta.removed],
        ),
        {item.id: item for item in kept.values()},
        tuple(collided),
    )


def _drifted(
    store: Workspace, known: Sequence[RemoteItem], rows: Collection[str]
) -> set[str]:
    """The ids of the *known* files whose copy is gone from the mirror, or whose entry has no indexed row in *rows*.  Blocking.

    A row whose indexing failed is none, so the next sync lands the file again.
    """
    return {
        item.id
        for item in known
        if not (store.path / item.path).is_file()
        or Part(stem_path_from_reference(item.path), "description").path not in rows
    }


async def _fetch(
    source: MirroredSource, store: Workspace, item: RemoteItem, remote: RemoteItem
) -> Upload | None:
    """The upload of *item*'s content, read as the source names it in *remote*, ``None`` when it cannot be read."""
    try:
        # The stat comes first, so a write landing meanwhile refuses the upload.
        basis = await asyncio.to_thread(ContentStat.from_path, store.path / item.path)
        content = await source.connector.read(remote)
    except Exception:
        logger.warning(
            "Reading %s of source %s failed", item.path, source.id, exc_info=True
        )
        return None

    return Upload(
        Location(Root.of(store), item.path),
        content,
        modified_at=item.modified,
        origin="synced",
        overwrite=True,
        basis=basis,
    )


type _Fetched = list[Upload | None]


async def _fetch_all(
    source: MirroredSource,
    store: Workspace,
    batch: Sequence[RemoteItem],
    remote: Mapping[str, RemoteItem],
) -> _Fetched:
    """:func:`_fetch` every item of *batch*, a bounded number at a time."""
    return await bounded_gather(
        batch,
        lambda item: _fetch(source, store, item, remote.get(item.id, item)),
        limit=settings.jobs.collection_concurrency,
    )


def _present(root: Path, paths: Collection[str]) -> set[str]:
    """The *paths* that exist below *root*.  Blocking."""
    return {path for path in paths if (root / path).exists()}


@dataclass(frozen=True, slots=True)
class _Recorder:
    """Writes what a commit of the sync changed about the items, in that commit's transaction."""

    source_id: str

    def __call__(
        self,
        *,
        put: Sequence[RemoteItem] = (),
        drop: Collection[str] = (),
        held: Collection[str] | None = None,
    ) -> RowChange:
        source_id = self.source_id

        async def write(s: AsyncSession) -> None:
            await db_sources.drop_items(s, source_id, drop)

            if held is not None:
                await db_sources.set_held(s, source_id, held)

            await db_sources.put_items(s, source_id, put)

        return write


async def _commit(items: Sequence[Item], record: RowChange) -> None:
    """Commit *items* with *record*, a failure only to index what committed merely logged."""
    try:
        _ = await apply_changeset(items, host=True, record=record)
    except IndexingFailed:
        logger.warning(
            "Indexing a mirrored change failed, the next sync lands it again",
            exc_info=True,
        )


async def _land(
    uploads: Sequence[Upload], items: Mapping[str, RemoteItem], recorder: _Recorder
) -> list[Upload]:
    """Land *uploads* with their *items* in one commit, else each half the same way, returning the ones that landed.

    Halving keeps the commits, and the conversions each repeats, few when one file fails.
    """
    try:
        await _commit(
            uploads, recorder(put=[items[upload.target.path] for upload in uploads])
        )
    except Exception:
        if len(uploads) == 1:
            logger.warning(
                "Mirroring %s failed", uploads[0].target.canonical, exc_info=True
            )
            return []
    else:
        return list(uploads)

    half = len(uploads) // 2

    return [
        *await _land(uploads[:half], items, recorder),
        *await _land(uploads[half:], items, recorder),
    ]


@dataclass(slots=True)
class Progress:
    """How far a running sync got, ``done`` of ``total`` files."""

    done: int = 0
    total: int = 0
    announced: float = -_ANNOUNCE_SECONDS
    """When the admins were last told, on the monotonic clock."""

    def announce(self) -> None:
        """Tell the admins how far the sync got, at most once every :data:`_ANNOUNCE_SECONDS`."""
        if (now := time.monotonic()) - self.announced >= _ANNOUNCE_SECONDS:
            self.announced = now
            manager.publish(ADMINS, SourceChanged())


_progress: dict[str, Progress] = {}
"""The progress of the running syncs by their source."""


def progress(source_id: str) -> Progress | None:
    """The progress of the sync of *source_id*, None while none runs."""
    return _progress.get(source_id)


async def _apply(
    source: MirroredSource,
    store: Workspace,
    plan: SyncPlan,
    progress: Progress,
    remote: Mapping[str, RemoteItem] | None = None,
) -> SyncReport:
    """Delete, then move, then download in batches, recording the items in the commit of their files and the *progress*.

    A move onto a path still taken, as in a rename chain, deletes and downloads instead.
    """
    root = Root.of(store)
    recorder = _Recorder(source.id)
    downloads = list(plan.downloads)
    queued = {item.id for item in downloads}
    gone = list(plan.deletes)
    moves: list[Item] = []
    moved: list[RemoteItem] = []
    present = await asyncio.to_thread(
        _present,
        store.path,
        {item.path for item in gone}
        | {item.path for move in plan.moves for item in move},
    )

    for old, new in plan.moves:
        renamed = old.path in present and new.path not in present

        if renamed:
            moves.append(Move(Location(root, old.path), Location(root, new.path)))
            moved.append(replace(new, version=old.version))
            continue

        # Downloaded where it went instead, as when another file vacates that path only now.
        gone.append(old)

        if new.id not in queued:
            queued.add(new.id)
            downloads.append(new)

    progress.total = len(downloads)
    progress.announce()
    deletes: list[Item] = [
        Delete(Location(root, item.path)) for item in gone if item.path in present
    ]
    report = SyncReport(
        moved=len(moved),
        deleted=len(plan.deletes),
        held=len(plan.held),
        skipped=list(plan.skipped),
    )
    by_path = {item.path: item for item in downloads}
    batches = batched(downloads, _BATCH)
    fetching: deque[tuple[Sequence[RemoteItem], asyncio.Task[_Fetched]]] = deque()

    def prefetch() -> None:
        if batch := next(batches, None):
            fetching.append(
                (
                    batch,
                    asyncio.create_task(_fetch_all(source, store, batch, remote or {})),
                )
            )

    await _commit(
        deletes,
        recorder(
            drop=[item.id for item in plan.deletes],
            held=[item.id for item in plan.held],
        ),
    )
    await _commit(moves, recorder(put=moved))
    prefetch()

    try:
        while fetching:
            batch, task = fetching.popleft()
            fetched = await task
            prefetch()
            uploads = [upload for upload in fetched if upload is not None]
            landed = await _land(uploads, by_path, recorder) if uploads else []

            report.downloaded += len(landed)
            report.failed += len(batch) - len(landed)
            progress.done = report.downloaded + report.failed
            progress.announce()
    finally:
        for _, task in fetching:
            await cancel_and_drain(task)

    return report


async def _drift(store: Workspace, known: Sequence[RemoteItem]) -> set[str]:
    """The ids of the *known* files the mirror lost behind the sync's back, see :func:`_drifted`."""
    if not known:
        return set()

    rows = await db_documents.get_entry_states(store)
    indexed = {path for path, state in rows.items() if state.content_digest is not None}

    return await asyncio.to_thread(_drifted, store, known, indexed)


async def sync_source(
    source: MirroredSource, host: HostGrant, due: db_sources.Due
) -> SyncReport:
    """Bring the mirror of *source* up to date as the *host*, recording how it went.

    A *due* request to confirm lists the source in full and deletes what the brake would hold,
    and so does a sync that finds the mirror lost a file, which it downloads again.

    Raises:
        Exception: Whatever the connector or the gateway raised, recorded as
            the source's error.
    """
    store = host.workspace(SourceScope(source.id))
    states = await db_sources.load_states()
    cursor = states.get(source.id, db_sources.SourceState()).cursor
    confirmed = due.request == "confirm"
    progress = _progress[source.id] = Progress()

    try:
        known, held = await asyncio.gather(
            db_sources.load_items(source.id), db_sources.load_held(source.id)
        )
        stale = await _drift(store, known)

        if stale:
            logger.warning(
                "The mirror of source %s lost %d files, which it downloads again",
                source.id,
                len(stale),
            )

        spelled = _spell(
            await source.connector.delta(None if confirmed or stale else cursor)
        )
        delta = spelled.delta
        plan = plan_sync(
            known,
            delta,
            held=held,
            stale=stale,
            max_sweep_share=source.config.max_sweep_share,
            confirmed=confirmed,
        )
        report = await _apply(source, store, plan, progress, spelled.remote)
    except Exception as exc:
        await db_sources.finish_sync(
            source.id, due, cursor=cursor, error=failure_text(exc)
        )
        raise
    finally:
        _ = _progress.pop(source.id, None)

    report.refused.extend(delta.refused)
    report.collided.extend(spelled.collided)
    notes = [
        *([_refused(report.refused).current] if report.refused else []),
        *([_failed(report.failed).current] if report.failed else []),
        *([_collided(report.collided).current] if report.collided else []),
    ]
    await db_sources.finish_sync(
        source.id,
        due,
        cursor=cursor if report.failed or report.refused else delta.cursor,
        error=" ".join(notes) or None,
    )
    logger.info("Synced source %s: %s", source.id, report)

    if report.held:
        logger.warning(
            "Held the deletion of %d files of source %s for an admin to confirm",
            report.held,
            source.id,
        )

    return report


async def request_sync(source: MirroredSource, *, confirm: bool = False) -> None:
    """Ask for a sync of *source* now, *confirm* deleting what its brake held."""
    await db_sources.request_sync(source.id, confirm=confirm)
    _wake.set()
    manager.publish(ADMINS, SourceChanged())


async def _run(source: MirroredSource, due: db_sources.Due) -> None:
    """Sync *source* as *due*, logging a failure."""
    try:
        _ = await sync_source(source, HostGrant(), due)
    except Exception:
        logger.warning("Syncing source %s failed", source.id, exc_info=True)
    finally:
        _ = _tasks.pop(source.id, None)
        manager.publish(ADMINS, SourceChanged())


async def _schedule() -> NoReturn:
    """Start a sync of every source that is due and not syncing, now and then every tick or request."""
    while True:
        _wake.clear()

        try:
            for source in catalog.SOURCES.values():
                if source.id in _tasks:
                    continue

                due = await db_sources.due(
                    source.id,
                    interval=timedelta(minutes=source.config.interval_minutes),
                )

                if due is not None:
                    _tasks[source.id] = asyncio.create_task(_run(source, due))
        except Exception:
            logger.warning("Scheduling the source syncs failed", exc_info=True)

        with suppress(TimeoutError):
            await asyncio.wait_for(_wake.wait(), _TICK_SECONDS)


def _prune_mirrors(keep: Collection[str]) -> None:
    """Remove the mirror of every source but those of *keep*.

    A hidden name is none, such as the staging area of a change in flight,
    which its journal settles (:func:`hivegent.workspace.recover_changesets`).
    """
    mirrors = settings.data_dir / "sources"

    if mirrors.is_dir():
        for mirror in mirrors.iterdir():
            if not mirror.name.startswith(".") and mirror.name not in keep:
                remove_path(mirror)


async def prune_sources(*, keep_held: bool = True) -> None:
    """Drop every source no longer configured, with its documents and its mirror.

    With *keep_held*, as at every boot, a source that holds a file its source
    lost is kept with its mirror, the only copy of that file, so a mistake in
    the configuration loses nothing, and ``hivegent reset sources`` drops it.
    No user sees a source no longer configured, so nothing it keeps is searched.
    """
    gone, kept = await db_sources.prune_sources(catalog.SOURCES, keep_held=keep_held)

    if gone:
        logger.info("Dropped the sources no longer configured: %s", ", ".join(gone))

    if kept:
        logger.warning(
            "Kept the sources no longer configured that hold files their source lost: %s."
            " Configure them again, or drop them with `hivegent reset sources`",
            ", ".join(kept),
        )

    await asyncio.to_thread(_prune_mirrors, {*catalog.SOURCES, *kept})


@asynccontextmanager
async def sources_lifespan() -> AsyncGenerator[None]:
    """Sync the sources in the background while the app runs and close their clients after."""
    if not catalog.SOURCES:
        yield
        return

    async with CLIENTS:
        try:
            async with background(asyncio.create_task(_schedule())):
                yield
        finally:
            _ = await asyncio.gather(
                *(cancel_and_drain(task) for task in tuple(_tasks.values()))
            )
