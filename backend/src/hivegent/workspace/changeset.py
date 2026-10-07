"""The one gateway every write, edit, move, delete, and new directory commits through.

A :class:`~hivegent.changes.Changeset` routed to
:class:`~hivegent.workspace.operations.Location`\\ s, a casebase and the path
local to its workspace, is a batch of operations (:mod:`hivegent.changes`):

* ``Write`` sets a text document's content (``mode`` replace, create,
  append, or prepend), creating it when it is missing.
* ``Edit`` applies a list of exact-string ``TextEdit`` in order and
  lands them as one write and one reindex.
* ``Move`` relocates an entry (its description, original, and assets) or
  a whole directory, within one workspace or across two.
* ``Delete`` removes an entry or a directory, refusing the other kind when
  it states which it ``expect``\\ s.
* ``CreateDir`` creates an empty directory.

The items apply at once, the way a diff of two states does: every source and
every basis names the workspace as it is now, and every destination and write
target the workspace once the changeset applied.  So chains (``a→b``,
``b→c``), swaps and rotations, a move onto a path another item vacates, moves
and deletes inside a directory another item moves, and a write to a file a
move carries all fit in one changeset, and the order of the items only decides
the order of their reports.

A basis is what a caller saw when it last read a file: the content hash the
read tools report, or the file's :class:`~hivegent.entries.ContentStat`.  The
change is refused with a 409 when the file moved on since, and
``Write(mode="create")`` is the basis of a file that must not exist yet.

:func:`plan_changeset` resolves and validates a changeset without touching
anything.  Every item claims *units* of the current workspace (an entry with
its description, original, and assets, or a directory as a whole tree) and
fills paths of the final one.  A moved unit carries everything below it unless
a nested item moves or deletes that part on its own.  The final state is then
checked: each current path is claimed by one item at most (nesting aside),
each final path is filled by one item at most, and every destination is free
or vacated by the changeset itself.  A move destination naming a directory
that stays where it is means into it, like ``mv``.  Paths compare the way the
workspace's filesystem compares them, case-insensitively where it is, and an
existing path is respelled the way the disk spells it.  The rows of every
source are fetched in one query, and everything that reads the disk runs in a
worker thread.  It returns the resolved changeset, plain data that
``pydantic.TypeAdapter(Changeset[Location])`` round-trips, so a caller can
persist it and apply it later, with the :class:`~hivegent.changes.ChangesetSummary`
a person approving it reads.

:func:`apply_changeset` commits a changeset all or nothing:

1. Resolve every item and convert the text originals whose markdown projection
   has to be regenerated, concurrently and without any lock.
2. Take the locks of every casebase involved, in ``store_key`` order.
3. Resolve again and refuse with a 409 when a text item's file changed while
   it was being prepared.
4. Install every file change through one rename journal and apply every row
   change in one SQL transaction, so a failure in either restores the files
   and rolls the transaction back.  Deleted and moved units leave first,
   deepest first, a moved one parked in the staging area with its rows on a
   temporary stem.  The moved ones then land, shallowest first, and the writes and
   new directories follow.  Rows are only ever updated, never recreated, so a
   moved document keeps its id and its embeddings, and the temporary stem
   (:data:`_PARK_STEM`) exists only inside that transaction.
5. Claim the written entries as in flight and release the locks.
6. Chunk and index what was written, one entry after another's assets but
   the entries concurrently, release the claims, and announce every changed
   workspace once to the *owner* the caller names.

It returns one report per item.  A failure in the last step leaves the new
files in place without their chunks, which a null ``content_digest`` marks for
the startup reconcile, and raises.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from functools import cache, partial
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Self
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..changes import (
    MAX_DIFF_CHARS,
    MAX_SUMMARY_DIFF_CHARS,
    Basis,
    Changeset,
    ChangesetSummary,
    CreateDir,
    Delete,
    DeleteKind,
    Edit,
    FileDiff,
    Move,
    Operation,
    PathMove,
    Write,
    capped_diff,
)
from ..chunkers import ChunkingSpec
from ..chunkers.base import EntryMetadata
from ..concurrency import bounded_gather, shield_to_completion
from ..config import sanitize_document_path, settings
from ..converters import writes_as_text
from ..db import documents as db_documents
from ..db import engine as db_engine
from ..entries import (
    ContentStat,
    EntryPaths,
    Listdir,
    assets_dir_for_stem,
    description_path_for_stem,
    folds_case,
    is_below,
    is_description_file,
    list_names,
    path_key,
    rebase,
    repoint_asset_refs,
    respell,
    stem_path_from_reference,
)
from ..l10n import Localized
from ..llm_config import LlmConfig
from ..store import Casebase
from ..types import PipelineSpec
from ..workspace_events import announce_paths
from .commit import (
    _DOCUMENT_EXISTS,
    _entry_paths,
    _is_existing_directory,
    _remove_entry_files,
    _remove_entry_rows,
    _stage_prepared,
    _written_entries,
)
from .documents import (
    _already_exists,
    _binary_write,
    _check_basis,
    _decode_existing,
    _regenerated,
    derive_text,
    edit_mutation,
    write_mutation,
)
from .indexing import chunk_and_index_document
from .locks import (
    _add_inflight,
    _discard_inflight,
    _locked,
    _reject_if_inflight,
    _reject_if_scope_inflight,
)
from .operations import Location, route
from .paths import (
    DIRECTORY_PATH_REQUIRED,
    _check_not_reserved_path,
    _enforce_file_size,
    _Journal,
    _journaled,
    _parent_is_file,
    _shown,
    _write_workspace_file,
    directory_not_found,
    document_not_found,
)
from .prepare import _prepare_upload, _PreparedUpload, _Reserved

__all__ = ["Gateway", "PlannedChangeset", "apply_changeset", "plan_changeset"]

logger = logging.getLogger(__name__)

_PARK_STEM = ".changeset-park"
"""The stem a moved unit's rows wait on between leaving and landing.

Never on disk: the rows pass through it inside the one transaction that moves them, so no listing,
index, or reconcile can meet it.
"""


def _moved(source: str, destination: str) -> Localized[str]:
    return Localized(
        en=f"Moved '{source}' to '{destination}'.",
        de=f"„{source}“ nach „{destination}“ verschoben.",
    )


def _deleted(path: str) -> Localized[str]:
    return Localized(en=f"Deleted '{path}'.", de=f"„{path}“ gelöscht.")


def _created_directory(path: str) -> Localized[str]:
    return Localized(en=f"Created '{path}'.", de=f"„{path}“ erstellt.")


def _destination_exists_at(path: str) -> Localized[str]:
    return Localized(
        en=f"Destination already exists: {path}",
        de=f"Das Ziel existiert bereits: {path}",
    )


def _claimed_twice(path: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' takes part in more than one change: a path may be moved "
            "or deleted once and filled once per call"
        ),
        de=(
            f"„{path}“ ist an mehr als einer Änderung beteiligt: Ein Pfad darf "
            "pro Aufruf einmal verschoben oder gelöscht und einmal befüllt werden"
        ),
    )


def _changed_meanwhile(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' changed while this change was being prepared. Retry it",
        de=f"„{path}“ wurde geändert, während diese Änderung vorbereitet wurde. Versuche es erneut",
    )


def _same_paths(path: str) -> Localized[str]:
    return Localized(
        en=(
            f"Source and destination are the same: {path}. A document's extension "
            "is derived from the entry and cannot change by moving"
        ),
        de=(
            f"Quelle und Ziel sind identisch: {path}. Die Dateiendung eines Dokuments "
            "ergibt sich aus dem Eintrag und lässt sich durch Bewegen nicht ändern"
        ),
    )


def _into_itself(path: str) -> Localized[str]:
    return Localized(
        en=f"Cannot move a directory into itself: {path}",
        de=f"Ein Ordner kann nicht in sich selbst bewegt werden: {path}",
    )


_NOT_FOUND: Mapping[DeleteKind, Callable[[str], Localized[str]]] = {
    "dir": directory_not_found,
    "entry": document_not_found,
}


# ─── Resolution ───────────────────────────────────────────────────────


def _workspace(store: Casebase) -> Path:
    """Non-creating: a rejected change must not leave an empty workspace behind."""
    return store.workspace_path(settings.data_dir)


def _depth(path: str) -> int:
    return len(PurePosixPath(path).parts)


type _Key = tuple[str, str]


@dataclass(slots=True, frozen=True)
class _Unit:
    """What one item moves, deletes, or lands: an entry, or a whole tree.

    A tree is a directory.
    """

    store: Casebase
    path: str
    """The entry's stem, or the tree's root."""
    entry: EntryPaths | None = None

    @property
    def files(self) -> tuple[str, ...]:
        """The workspace paths the unit consists of, everything below them included."""
        return (self.path,) if self.entry is None else self.entry.files

    def at(self, store: Casebase, path: str) -> Self:
        """The unit relocated to *path* in *store*, its companions renamed along."""
        entry = None if self.entry is None else self.entry.at(path)

        return replace(self, store=store, path=path, entry=entry)


type _Claims = dict[_Key, tuple[int, _Unit]]
"""Units by every path they consist of, with the item that claims each."""


@dataclass(slots=True, frozen=True)
class _Plan:
    """The units a changeset vacates and lands, and the paths it fills.

    One plan is one pass over a workspace that does not change meanwhile, so
    it caches what it reads of the disk: each store's case folding and each
    directory's listing.
    """

    vacated: _Claims = field(default_factory=dict)
    landed: _Claims = field(default_factory=dict)
    moves: dict[int, tuple[_Unit, _Unit]] = field(default_factory=dict)
    filled: dict[_Key, int] = field(default_factory=dict)
    written: dict[_Key, Location] = field(default_factory=dict)
    """The files text items write, which exist nowhere yet for the disk to show."""
    originals: list[_Unit] = field(default_factory=list)
    """The new originals, each claiming a stem once every item is resolved."""
    guards: list[Callable[[], None]] = field(default_factory=list)
    """In-flight checks, run on the event loop that changes what they read."""
    folded: dict[str, bool] = field(default_factory=dict)
    listdir: Listdir = field(default_factory=lambda: cache(list_names))

    def key(self, store: Casebase, path: str) -> _Key:
        """What two spellings of one path share, compared as the store's filesystem does."""
        folded = self.folded.get(store.store_key)

        if folded is None:
            folded = self.folded[store.store_key] = folds_case(_workspace(store))

        return store.store_key, path_key(path, folded=folded)

    def check_inflight(self) -> None:
        """Run and clear the in-flight checks collected so far."""
        for guard in self.guards:
            guard()

        self.guards.clear()

    def sanitized(self, location: Location, *, keep_name: bool = False) -> Location:
        """*location* made safe and spelled the way the disk spells what exists of it.

        *keep_name* leaves the last segment as given, which is how a destination
        renames something case-only on a case-insensitive filesystem.
        """
        try:
            path = PurePosixPath(sanitize_document_path(location.path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        root = _workspace(location.store)

        if keep_name:
            spelled = PurePosixPath(respell(root, str(path.parent), self.listdir), path.name)

            return Location(location.store, str(spelled))

        return Location(location.store, respell(root, str(path), self.listdir))

    def holder(
        self, claims: _Claims, store: Casebase, path: str, skip: int | None = None
    ) -> tuple[int, _Unit] | None:
        """The deepest unit in *claims* holding *path*, ignoring the item *skip*."""
        pure = PurePosixPath(path)

        for candidate in (pure, *pure.parents):
            hit = claims.get(self.key(store, str(candidate))) if candidate.parts else None

            if hit is not None and hit[0] != skip:
                return hit

        return None

    def carry(self, unit: _Unit, path: str, to: _Unit) -> str | None:
        """Where *path* lies once *unit* is *to*, ``None`` if the unit does not hold it."""
        parts = PurePosixPath(path).parts

        for old, new in zip(unit.files, to.files, strict=True):
            held = str(PurePosixPath(*parts[: _depth(old)]))

            if self.key(unit.store, held) == self.key(unit.store, old):
                return rebase(path, held, new)

        return None

    def claim(self, claims: _Claims, index: int, unit: _Unit) -> None:
        for path in unit.files:
            key = self.key(unit.store, path)

            if key in claims and claims[key][0] != index:
                raise HTTPException(
                    status_code=400, detail=_claimed_twice(_shown(unit.store, path)).current
                )

            claims[key] = (index, unit)

    def fill(self, index: int, target: Location) -> None:
        key = self.key(target.store, target.path)

        if self.filled.setdefault(key, index) != index:
            raise HTTPException(
                status_code=400, detail=_claimed_twice(target.canonical).current
            )

    def claim_write(self, index: int, unit: _Unit) -> None:
        """Claim the files a text item writes, an original's projection included.

        They share one folder and one stem, so both are checked once.
        """
        store, first = unit.store, unit.files[0]
        self.check_parents(store, first)
        self.guards.append(partial(_reject_if_inflight, store, first))

        for path in unit.files:
            target = Location(store, path)
            self.fill(index, target)
            self.written[self.key(store, path)] = target

    def origin(
        self, store: Casebase, path: str, skip: int | None = None
    ) -> Location | None:
        """The current path whose content lies at *path* once the changeset applied.

        ``None`` when nothing of the current workspace ends up there.  *skip*
        ignores one item's own landing, to ask what else would be there.
        """
        landing = self.holder(self.landed, store, path, skip)

        if landing is not None:
            index, destination = landing
            source = self.moves[index][0]
            initial = self.carry(destination, path, source)

            if initial is None:
                return None

            owner = self.holder(self.vacated, source.store, initial)

            return Location(source.store, initial) if owner and owner[0] == index else None

        return None if self.holder(self.vacated, store, path) else Location(store, path)

    def occupied(self, store: Casebase, path: str, skip: int | None = None) -> Path | None:
        """What is on disk now and ends up at *path*, besides the landing of *skip*."""
        origin = self.origin(store, path, skip)
        full = None if origin is None else origin.full_path

        return full if full is not None and full.exists() else None

    def check_parents(self, store: Casebase, path: str) -> None:
        """Refuse a final path below one that is a file once the changeset applied."""
        for parent in PurePosixPath(path).parents:
            occupant = self.occupied(store, str(parent)) if parent.parts else None

            if occupant is not None and occupant.is_file():
                raise HTTPException(
                    status_code=409,
                    detail=_parent_is_file(_shown(store, str(parent))).current,
                )

    def check_written_parents(self) -> None:
        """Refuse a final path below a file another item writes.

        :meth:`check_parents` sees the disk and the moves, while a written file
        exists nowhere until the journal makes it, so an extension-less ``a``
        written beside a landing ``a/b.md`` is caught here, once every item
        claimed its paths, rather than by the journal.
        """
        for store_key, path in (*self.filled, *self.landed):
            for parent in PurePosixPath(path).parents:
                target = self.written.get((store_key, str(parent)))

                if target is not None:
                    raise HTTPException(
                        status_code=409, detail=_parent_is_file(target.canonical).current
                    )

    def _final_names(self, store: Casebase, directory: str) -> set[str]:
        """Every name *directory* holds once the changeset applied.

        What the disk holds there now or where it comes from, and what lands or
        is written into it.
        """
        source = self.origin(store, directory)
        parent = self.key(store, directory)
        arriving = (
            *(
                Location(unit.store, path)
                for _source, unit in self.moves.values()
                for path in unit.files
            ),
            *self.written.values(),
        )

        return {
            *(self.listdir(source.full_path) if source is not None else ()),
            *(
                PurePosixPath(other.path).name
                for other in arriving
                if other.store == store
                and self.key(store, str(PurePosixPath(other.path).parent)) == parent
            ),
        }

    def check_slot(self, unit: _Unit) -> None:
        """Refuse a new original whose stem holds another entry once the changeset applied.

        An upload's slot check, asked of the final state like every other check
        here: a stem the changeset vacates is free, and one it moves an entry
        onto, or writes another part of, is not.
        """
        store, stem = unit.store, PurePosixPath(unit.path)

        for path in unit.files:
            occupant = self.occupied(store, path)

            if occupant is not None:
                detail = (
                    _is_existing_directory(_shown(store, path))
                    if occupant.is_dir()
                    else _DOCUMENT_EXISTS
                )
                raise HTTPException(status_code=409, detail=detail.current)

        own = {self.key(store, path) for path in unit.files}
        name = self.key(store, stem.name)

        for sibling_name in self._final_names(store, str(stem.parent)):
            sibling = str(stem.parent / sibling_name)
            key = self.key(store, sibling)

            if (
                key not in own
                and self.key(store, PurePosixPath(sibling_name).stem) == name
                and (key in self.written or self.occupied(store, sibling))
            ):
                raise HTTPException(status_code=409, detail=_DOCUMENT_EXISTS.current)


@dataclass(slots=True, frozen=True)
class _TextEffect:
    """A text document's new content, derived from the content it replaces."""

    target: Location
    current: str | None
    content: str
    chunking: ChunkingSpec | None
    origin: Location | None
    """Where *current* lies before the changeset, ``None`` for nothing."""

    @property
    def is_original(self) -> bool:
        """Whether the write lands on an original whose projection is re-derived."""
        return not is_description_file(self.target.path)


@dataclass(slots=True, frozen=True)
class _Vacate:
    """A unit that leaves its place, landing at *destination* or deleted."""

    unit: _Unit
    destination: _Unit | None


@dataclass(slots=True, frozen=True)
class _MakeDir:
    target: Location


type _Effect = _TextEffect | _Vacate | _MakeDir


@dataclass(slots=True, frozen=True)
class _Resolved:
    """One validated item: its resolved form, what it does, and its report."""

    operation: Operation[Location]
    effect: _Effect
    report: str


type _Sources = dict[int, tuple[Location, bool]]
"""Each move's and delete's source, spelled as the disk does, and whether it is a directory."""


def _source(
    plan: _Plan,
    location: Location,
    basis: Basis | None,
    expect: DeleteKind | None,
    *,
    moving: bool,
) -> tuple[Location, bool]:
    location = plan.sanitized(location)
    store, path = location.store, location.path
    full = location.full_path
    _check_basis(_shown(store, path), full, basis)
    is_dir = full.is_dir()
    if expect is not None and expect != ("dir" if is_dir else "entry"):
        raise HTTPException(
            status_code=404, detail=_NOT_FOUND[expect](_shown(store, path)).current
        )

    if is_dir:
        if moving:
            _check_not_reserved_path(path)

        plan.guards.append(partial(_reject_if_scope_inflight, store, path))
    else:
        plan.guards.append(partial(_reject_if_inflight, store, path))

    return location, is_dir


def _locate_sources(plan: _Plan, operations: list[Operation[Location]]) -> _Sources:
    """Spell every move's and delete's source as the disk does, in place."""
    sources: _Sources = {}

    for index, op in enumerate(operations):
        match op:
            case Move(source=source, basis=basis):
                sources[index] = _source(plan, source, basis, None, moving=True)
                operations[index] = replace(op, source=sources[index][0])
            case Delete(target=target, basis=basis, expect=expect):
                if not target.path:
                    # A bare scope root would wipe the workspace, and the full wipe of
                    # files and rows together is `delete_all`.
                    raise HTTPException(
                        status_code=400, detail=DIRECTORY_PATH_REQUIRED.current
                    )

                sources[index] = _source(plan, target, basis, expect, moving=False)
                operations[index] = replace(op, target=sources[index][0])
            case _:
                pass

    return sources


def _landing(operation: Move[Location], index: int, source: _Unit, plan: _Plan) -> Move[Location]:
    """Resolve where a move lands, and claim it."""
    destination = operation.destination

    if destination.path:
        destination = plan.sanitized(destination, keep_name=True)

    store, path = destination.store, destination.path
    same_store = store == source.store

    named = source.entry.description_path if source.entry else source.path

    if not path or (
        destination.full_path.is_dir() and plan.holder(plan.vacated, store, path) is None
    ):
        root = _workspace(store)
        path = str(PurePosixPath(respell(root, path, plan.listdir), PurePosixPath(named).name))

    if source.entry is None:
        if same_store and is_below(plan.key(store, path)[1], plan.key(store, source.path)[1]):
            raise HTTPException(
                status_code=400, detail=_into_itself(_shown(source.store, source.path)).current
            )
        _check_not_reserved_path(path)
        plan.guards.append(partial(_reject_if_scope_inflight, store, path))
    else:
        path = stem_path_from_reference(path)
        _check_not_reserved_path(path)
        plan.guards.append(partial(_reject_if_inflight, store, description_path_for_stem(path)))

    if same_store and path == source.path:
        raise HTTPException(
            status_code=400, detail=_same_paths(_shown(store, path)).current
        )

    target = source.at(store, path)
    plan.moves[index] = source, target
    plan.claim(plan.landed, index, target)
    # The destination named is the counterpart of the path the source was
    # addressed by, so a move of an original reads back as one.
    moved_to = plan.carry(source, operation.source.path, target) or target.files[0]

    return replace(operation, destination=Location(store, moved_to))


def _check_landing(index: int, destination: _Unit, plan: _Plan) -> None:
    for path in destination.files:
        if plan.occupied(destination.store, path, skip=index) is not None:
            raise HTTPException(
                status_code=409,
                detail=_destination_exists_at(_shown(destination.store, path)).current,
            )

        plan.check_parents(destination.store, path)


def _resolve_text(operation: Write[Location] | Edit[Location], index: int, plan: _Plan) -> _Resolved:
    target = plan.sanitized(operation.target)
    store, path = target.store, target.path
    shown = _shown(store, path)
    # A file a move carries here is read where it lies until the move lands it.
    origin = plan.origin(store, path)
    mutate = (
        write_mutation(shown, operation.content, operation.mode)
        if isinstance(operation, Write)
        else edit_mutation(shown, operation.edits)
    )
    current, content, report = derive_text(
        None if origin is None else origin.full_path, shown, mutate, operation.basis
    )
    _enforce_file_size(content.encode("utf-8"))
    effect = _TextEffect(
        target,
        current,
        content,
        operation.chunking if isinstance(operation, Write) else None,
        origin,
    )
    stem = stem_path_from_reference(path)
    unit = _Unit(store, stem, EntryPaths(stem, path, None, None))

    if effect.is_original:
        if current is None and not writes_as_text(path):
            raise HTTPException(status_code=400, detail=_binary_write(shown).current)

        # An original's write regenerates its projection, so the item fills both.
        unit = _Unit(store, stem, EntryPaths(stem, description_path_for_stem(stem), path, None))

        # Creating an original claims the whole stem: requiring a free one keeps
        # the write from superseding another entry's description or original.
        if current is None:
            plan.originals.append(unit)

        report = _regenerated(report, _shown(store, unit.files[0])).current

    plan.claim_write(index, unit)

    return _Resolved(replace(operation, target=target), effect, report)


def _resolve_directory(operation: CreateDir[Location], index: int, plan: _Plan) -> _Resolved:
    if not operation.target.path:
        raise HTTPException(status_code=400, detail=DIRECTORY_PATH_REQUIRED.current)

    target = plan.sanitized(operation.target)
    store, path = target.store, target.path
    _check_not_reserved_path(path)
    plan.guards.append(partial(_reject_if_inflight, store, path))

    if plan.occupied(store, path) is not None:
        raise HTTPException(
            status_code=409, detail=_already_exists(target.canonical).current
        )

    plan.check_parents(store, path)
    plan.fill(index, target)

    return _Resolved(
        replace(operation, target=target),
        _MakeDir(target),
        _created_directory(target.canonical).current,
    )


def _settle(
    plan: _Plan,
    operations: list[Operation[Location]],
    sources: _Sources,
    rows: Mapping[tuple[Casebase, str], EntryMetadata],
) -> list[_Resolved]:
    """Resolve every item from its located source and rows, sources first."""
    units: dict[int, _Unit] = {}

    for index, (location, is_dir) in sources.items():
        store, path = location.store, location.path

        if is_dir:
            units[index] = _Unit(store, path)
        else:
            row = rows.get((store, stem_path_from_reference(path)))
            entry = _entry_paths(store, path, row)
            units[index] = _Unit(store, entry.stem_path, entry)

        plan.claim(plan.vacated, index, units[index])

    # Every destination depends on what all the sources vacate.
    for index, op in enumerate(operations):
        if isinstance(op, Move):
            operations[index] = _landing(op, index, units[index], plan)

    for index, (_source_unit, destination) in plan.moves.items():
        _check_landing(index, destination, plan)

    resolved: list[_Resolved] = []

    for index, op in enumerate(operations):
        match op:
            case Move(source=source, destination=destination):
                effect = _Vacate(*plan.moves[index])
                report = _moved(source.canonical, destination.canonical).current
                resolved.append(_Resolved(op, effect, report))
            case Delete(target=target):
                effect = _Vacate(units[index], None)
                resolved.append(_Resolved(op, effect, _deleted(target.canonical).current))
            case Write() | Edit():
                resolved.append(_resolve_text(op, index, plan))
            case CreateDir():
                resolved.append(_resolve_directory(op, index, plan))

    plan.check_written_parents()

    for unit in plan.originals:
        plan.check_slot(unit)

    return resolved


async def _resolve_all(changeset: Changeset[Location]) -> tuple[list[_Resolved], _Plan]:
    """Resolve every item: the disk in worker threads, the rows in between in one query."""
    plan = _Plan()
    operations = list(changeset.operations)
    sources = await asyncio.to_thread(_locate_sources, plan, operations)
    plan.check_inflight()
    rows = await db_documents.get_entries_metadata(
        (location.store, location.path)
        for location, is_dir in sources.values()
        if not is_dir
    )
    resolved = await asyncio.to_thread(_settle, plan, operations, sources, rows)
    plan.check_inflight()

    return resolved, plan


def _shown_files(unit: _Unit) -> tuple[str, ...]:
    """The paths of *unit* a person sees go: a tree's root, an entry's files on disk.

    An entry's description and original, or its description alone when only
    its row is left of it.
    """
    if (entry := unit.entry) is None:
        return (unit.path,)

    root = _workspace(unit.store)
    present = (entry.description_path, entry.original_path)

    return tuple(
        path for path in present if path is not None and (root / path).exists()
    ) or (entry.description_path,)


def _summarize(resolved: Sequence[_Resolved], plan: _Plan) -> ChangesetSummary:
    """What *resolved* does, as a person approving it reads it.  Blocking."""
    creates: list[FileDiff] = []
    updates: list[FileDiff] = []
    moves: list[PathMove] = []
    deletes: list[str] = []
    mkdirs: list[str] = []
    budget = MAX_SUMMARY_DIFF_CHARS

    def replaces(store: Casebase, path: str) -> bool:
        """Whether a file there now goes, deleted by another item."""
        hit = plan.holder(plan.vacated, store, path)
        effect = None if hit is None else resolved[hit[0]].effect

        return (
            isinstance(effect, _Vacate)
            and effect.destination is None
            and Location(store, path).full_path.exists()
        )

    for item in resolved:
        match item.effect:
            case _TextEffect(target=target, current=current, content=content):
                shown = target.canonical
                diff = capped_diff(shown, current, content, min(budget, MAX_DIFF_CHARS))
                budget -= len(diff)
                (creates if current is None else updates).append(FileDiff(shown, diff))
            case _Vacate(unit=unit, destination=None):
                deletes.extend(_shown(unit.store, path) for path in _shown_files(unit))
            case _Vacate(unit=unit, destination=_Unit() as destination):
                moved = dict(zip(unit.files, destination.files, strict=True))
                moves.extend(
                    PathMove(
                        _shown(unit.store, path),
                        _shown(destination.store, moved[path]),
                        is_dir=unit.entry is None,
                        replaces=replaces(destination.store, moved[path]),
                    )
                    for path in _shown_files(unit)
                )
            case _MakeDir(target=target):
                mkdirs.append(target.canonical)

    return ChangesetSummary(
        tuple(creates), tuple(updates), tuple(moves), tuple(deletes), tuple(mkdirs)
    )


@dataclass(slots=True, frozen=True)
class PlannedChangeset:
    """A changeset the gateway would commit, and what approving it shows.

    Attributes:
        changeset: The operations with sanitized paths and every move
            destination resolved to the path it lands on, ready to be stored
            and applied.
        summary: The creates and updates with their capped diffs, the moves
            of every file and directory, the deletes with every companion they
            take along, and the new directories.
    """

    changeset: Changeset[Location]
    summary: ChangesetSummary


async def plan_changeset(changeset: Changeset[Location]) -> PlannedChangeset:
    """Resolve and validate *changeset* without changing anything.

    Args:
        changeset: The operations to check, as a caller spelled them.

    Returns:
        The resolved changeset and its summary.

    Raises:
        HTTPException: When an item is invalid on its own (a missing source,
            an occupied destination, a reserved path, a stale basis) or
            claims a path another item claims.
    """
    resolved, plan = await _resolve_all(changeset)

    return PlannedChangeset(
        Changeset(tuple(item.operation for item in resolved)),
        await asyncio.to_thread(_summarize, resolved, plan),
    )


# ─── Application ──────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class _PreparedOriginal:
    """A text original's regenerated projection, converted ahead of the lock."""

    reserved: _Reserved
    upload: _PreparedUpload


@dataclass(slots=True, frozen=True)
class _Indexing:
    """A projection on disk awaiting its chunks, with the stat it was written at."""

    store: Casebase
    description_path: str
    markdown: str
    chunking: ChunkingSpec | None
    stat: ContentStat | None
    entry_metadata: EntryMetadata | None = None


async def _prepare_original(effect: _TextEffect) -> _PreparedOriginal:
    """Run a text original's new bytes through the upload conversion.

    The same pipeline as an upload of the edited file, so the projection left
    behind is byte for byte the one uploading it would produce, and a failed
    conversion leaves the previous entry untouched.
    """
    store, path = effect.target.store, effect.target.path
    data = effect.content.encode("utf-8")
    # A carried original keeps the rows it has until the move lands them here.
    origin = effect.origin
    metadata = (
        await db_documents.get_entry_metadata(origin.store, origin.path) if origin else None
    )
    reserved = _Reserved(
        reference=path,
        content=data,
        origin=metadata.origin if metadata else "imported",
        original_path=path,
        original_content=data,
        preserve=effect.current is not None,
    )
    upload = await _prepare_upload(
        store,
        path,
        data,
        PipelineSpec(chunking=effect.chunking) if effect.chunking else PipelineSpec(),
        LlmConfig(),
        origin=reserved.origin,
        original_path=path,
        ctx=None,
        clearing_assets=reserved.preserve,
    )

    return _PreparedOriginal(reserved, upload)


type _RowChange = Callable[[AsyncSession], Awaitable[object]]
"""One row write, run inside the changeset's single transaction."""


def _install_text(
    effect: _TextEffect,
    journal: _Journal,
    staging: Path,
    prepared: _PreparedOriginal | None,
) -> list[_Indexing]:
    """Install a text item's files, returning one entry's projections in index order."""
    store, path = effect.target.store, effect.target.path
    workspace = _workspace(store)

    if prepared is not None:
        for change in _stage_prepared(staging, prepared.upload, prepared.reserved):
            journal.apply(workspace, change)

        return [
            _Indexing(
                store,
                written.entry.description_path,
                written.entry.markdown,
                effect.chunking,
                written.stat,
                written.entry.entry_metadata,
            )
            for written in _written_entries(workspace, prepared.upload)
        ]

    live = effect.target.full_path
    journal.install(
        _write_workspace_file(staging, path, effect.content.encode("utf-8")), live
    )

    return [
        _Indexing(
            store, path, effect.content, effect.chunking, ContentStat.from_path(live)
        )
    ]


def _text_rows(effect: _TextEffect, prepared: _PreparedOriginal | None) -> list[_RowChange]:
    """Drop the asset rows of a rewritten original, whose new projection supersedes them."""
    if prepared is None or not prepared.reserved.preserve:
        return []

    store = effect.target.store
    assets = assets_dir_for_stem(stem_path_from_reference(effect.target.path))

    return [lambda s: db_documents.delete_subtree(store, assets, s=s)]


def _move_rows(source: _Unit, destination: _Unit) -> list[_RowChange]:
    """Re-key a unit's rows, which keeps their ids, and so their chunks."""
    if source.entry is None:
        return [
            lambda s: db_documents.move_subtree(
                source.store, source.path, destination.store, destination.path, s=s
            )
        ]

    changes: list[_RowChange] = [
        lambda s: db_documents.move_document(
            source.store, source.path, destination.store, destination.path, s=s
        )
    ]
    old, new = source.entry.assets_dir, assets_dir_for_stem(destination.path)

    if old is not None:
        changes.append(
            lambda s: db_documents.move_subtree(
                source.store, old, destination.store, new, s=s
            )
        )

    return changes


type _Park = tuple[Path, _Unit]
"""Where a moved unit waits between leaving and landing: its files' directory, its rows' stem."""


def _leave(effect: _Vacate, journal: _Journal, park: _Park) -> list[_RowChange]:
    """Delete a unit, or park its files in a staging directory and its rows on a stem."""
    unit = effect.unit
    workspace = _workspace(unit.store)

    if effect.destination is not None:
        parked, stem = park

        for path in unit.files:
            if (workspace / path).exists():
                journal.rename(workspace / path, parked / PurePosixPath(path).name)

        return _move_rows(unit, stem)

    if (entry := unit.entry) is not None:
        _remove_entry_files(journal, workspace, entry)

        return [lambda s: _remove_entry_rows(s, unit.store, entry)]

    journal.remove(workspace / unit.path)

    return [lambda s: db_documents.delete_subtree(unit.store, unit.path, s=s)]


def _land(
    unit: _Unit, destination: _Unit, journal: _Journal, park: _Park, staging: Path
) -> list[_RowChange]:
    """Move a parked unit's files and rows to *destination*.

    The description addresses its payload as ``<stem>.assets/...``, so its
    references follow a changed basename.
    """
    parked, stem = park
    workspace = _workspace(destination.store)
    names = PurePosixPath(unit.path).name, PurePosixPath(destination.path).name
    entry = unit.entry
    description = entry.description_path if entry and entry.assets_dir else None

    for old, new in zip(unit.files, destination.files, strict=True):
        held = parked / PurePosixPath(old).name

        if not held.exists():
            continue

        if old == description and names[0] != names[1]:
            text = _decode_existing(held, _shown(unit.store, old)).text
            staged = _write_workspace_file(
                staging, new, repoint_asset_refs(text, *names).encode("utf-8")
            )
            journal.install(staged, workspace / new)
        else:
            journal.rename(held, workspace / new)

    return _move_rows(stem, destination)


async def _install_all(
    resolved: Sequence[_Resolved],
    prepared: Mapping[int, _PreparedOriginal],
    staging: Path,
    journal: _Journal,
) -> list[list[_Indexing]]:
    """Land every item's files and rows together, which the caller's journal restores on failure."""
    vacating = sorted(
        (
            (index, item.effect)
            for index, item in enumerate(resolved)
            if isinstance(item.effect, _Vacate)
        ),
        key=lambda pair: _depth(pair[1].unit.path),
        reverse=True,
    )
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
    rows: list[_RowChange] = []
    pending: list[list[_Indexing]] = []

    for index, effect in vacating:
        unit = effect.unit
        parks[index] = staging / "park" / str(index), unit.at(unit.store, f"{token}/{index}")
        rows += _leave(effect, journal, parks[index])

    for index, unit, destination in moving:
        rows += _land(unit, destination, journal, parks[index], staging / str(index))

    for index, item in enumerate(resolved):
        match item.effect:
            case _TextEffect() as effect:
                pending.append(
                    _install_text(effect, journal, staging / str(index), prepared.get(index))
                )
                rows += _text_rows(effect, prepared.get(index))
            case _MakeDir(target=target):
                journal.mkdir(target.full_path)
            case _Vacate():
                pass

    if rows:
        async with db_engine.session() as s:
            for change in rows:
                await change(s)

    return pending


async def _index_entry(projections: Sequence[_Indexing]) -> Exception | None:
    """Chunk and index one entry's projections in order, its assets before itself.

    Returns the first failure rather than raising it, so the other entries
    indexing alongside are not cancelled.
    """
    failure: Exception | None = None

    for item in projections:
        try:
            await chunk_and_index_document(
                item.store,
                item.description_path,
                item.markdown,
                item.chunking,
                stat=item.stat,
                entry_metadata=item.entry_metadata,
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


async def _index_all(pending: Sequence[Sequence[_Indexing]]) -> None:
    """Chunk and index every written entry concurrently.

    One failure does not strand the rest without chunks, and the first is raised
    once every projection had its turn.
    """
    failures = await bounded_gather(
        pending, _index_entry, limit=settings.jobs.collection_concurrency
    )
    first = next((failure for failure in failures if failure is not None), None)

    if first is not None:
        raise first


async def _commit(
    changeset: Changeset[Location],
    planned: Sequence[_Resolved],
    prepared: Mapping[int, _PreparedOriginal],
    owner: str | None,
    exclude_client: str | None,
) -> tuple[str, ...]:
    with ExitStack() as claims:
        async with _locked(*(location.store for location in changeset.locations)):
            resolved, _plan = await _resolve_all(changeset)

            for before, after in zip(planned, resolved, strict=True):
                if isinstance(after.effect, _TextEffect) and after.effect != before.effect:
                    shown = after.effect.target.canonical
                    raise HTTPException(
                        status_code=409, detail=_changed_meanwhile(shown).current
                    )

            with _journaled() as (staging, journal):
                pending = await _install_all(resolved, prepared, staging, journal)

            # Until its chunks land, a written entry stays hidden from
            # inventory reads and refuses every other mutation.
            for item in chain.from_iterable(pending):
                claims.callback(_discard_inflight, item.store, item.description_path)
                _add_inflight(item.store, item.description_path)

        try:
            await _index_all(pending)
        finally:
            if owner is not None:
                announce_paths(
                    owner,
                    *(location.canonical for location in changeset.locations),
                    exclude_client=exclude_client,
                )

    return tuple(item.report for item in resolved)


async def apply_changeset(
    changeset: Changeset[Location],
    *,
    owner: str | None = None,
    exclude_client: str | None = None,
) -> tuple[str, ...]:
    """Commit *changeset* all or nothing, see the module docstring for the phases.

    Args:
        changeset: The operations to apply, planned or as a caller spelled them.
        owner: The user whose clients hear about the change, or nobody when
            ``None``.  A group workspace's casebase names the group, not the
            user to tell, which is why the caller says.
        exclude_client: The client that asked, which re-reads on its own.

    Returns:
        One human-readable report per operation, in order.

    Raises:
        HTTPException: When the changeset is invalid (see
            :func:`plan_changeset`), or a text item's file changed while it
            was being prepared.
    """
    planned, _plan = await _resolve_all(changeset)
    originals = {
        index: item.effect
        for index, item in enumerate(planned)
        if isinstance(item.effect, _TextEffect) and item.effect.is_original
    }
    converted = await bounded_gather(
        originals.values(), _prepare_original, limit=settings.jobs.collection_concurrency
    )
    prepared = dict(zip(originals, converted, strict=True))

    # Shielded as one unit even though it releases the locks partway through,
    # so a cancel can never settle between the file swap and the index and
    # leave new markdown wearing its predecessor's rows.
    return await shield_to_completion(
        _commit(changeset, planned, prepared, owner, exclude_client)
    )


@dataclass(slots=True, frozen=True)
class Gateway:
    """The gateway bound to the stores a caller may change and the user it tells.

    The one place a surface's canonical paths meet the gateway, so the agent
    and the MCP surface supply only their stores, their gate, and the error
    type they raise.

    Attributes:
        stores: The casebases a canonical path may route to.
        owner: The user whose clients hear about an applied change.
    """

    stores: tuple[Casebase, ...]
    owner: str | None = None

    async def plan(self, changeset: Changeset[str]) -> PlannedChangeset:
        """Route and plan *changeset*, see :func:`plan_changeset`.

        Raises:
            ValueError: When a path names none of :attr:`stores`.
            HTTPException: When the gateway refuses the changeset.
        """
        return await plan_changeset(route(changeset, self.stores))

    async def apply(self, changeset: Changeset[str]) -> tuple[str, ...]:
        """Route and apply *changeset*, see :func:`apply_changeset`, returning one report per item.

        Raises:
            ValueError: When a path names none of :attr:`stores`.
            HTTPException: When the gateway refuses the changeset.
        """
        return await apply_changeset(route(changeset, self.stores), owner=self.owner)

    async def commit(self, changeset: Changeset[str]) -> str:
        """Apply *changeset* as :meth:`apply` does, its reports joined into one receipt."""
        return "\n".join(await self.apply(changeset))
