"""The one gateway every write, edit, move, delete, and new directory commits through.

A :class:`~hivegent.changes.Changeset` routed to
:class:`~hivegent.workspace.operations.Location`\\ s, a root and the path
local to it, is a batch of operations (:mod:`hivegent.changes`):

* ``Write`` sets a text document's content (``mode`` replace, create,
  append, or prepend), creating it when it is missing.
* ``Edit`` applies a list of exact-string ``TextEdit`` in order and
  lands them as one write and one reindex.
* ``Move`` relocates an entry (its description, original, and assets) or
  a whole directory, within one workspace or across two.
* ``Delete`` removes an entry or a directory, refusing the other kind when
  it states which it ``expect``\\ s.
* ``CreateDir`` creates an empty directory.

A :class:`~hivegent.workspace.operations.Root` answers for its own rules, so
both kinds go through the one planner and executor below: a casebase's
workspace, whose entries carry rows, an index, and in-flight claims, and a
:class:`~hivegent.workspace.operations.Folder` of plain files written directly,
such as a conversation's ``/tmp``.  A root without a store skips what only an
entry has: rows, a projection, in-flight checks, indexing, and announcements.
Its file is a unit without an entry, a move to or from it is refused, and what
a changeset adds to it is put to its
:class:`~hivegent.workspace.operations.Quota`, which may name files the host
evicts.  Every root keeps what its policy reserves from ordinary changes.

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
worker thread.  A directory move carrying what the caller's filter hides is
refused where its source is located, naming only the directory.  It returns
the resolved changeset with the :class:`~hivegent.changes.ChangesetSummary` a
person approving it reads, which leaves a folder's items out, since only a
workspace change is put to a person.

:func:`apply_changeset` commits a changeset all or nothing:

1. Resolve every item and convert the text originals whose markdown projection
   has to be regenerated, concurrently and without any lock, which is skipped
   when no item writes text in a workspace.
2. Take the locks of every root involved, in ``store_key`` order, so the tasks
   writing one folder (a tool, a program, a spilled result) check and write it
   one at a time.
3. Resolve again, so every text item is derived and every basis, path, filter,
   and quota checked under the locks, and refuse with a 409 when an original
   converted in step 1 changed meanwhile.
4. Install every file change through one rename journal in a worker thread,
   staged beside every root it changes so each step stays a rename, and apply
   every row change in one SQL transaction, so a failure in either restores
   the files and rolls the transaction back.  Deleted and moved units, and the
   files a quota evicts, leave first, deepest first, a moved one parked in the
   staging area with its rows on a temporary stem.  The moved ones then land,
   shallowest first, and the writes and new directories follow.  Rows are only
   ever updated, never recreated, so a moved document keeps its id and its
   embeddings, and the temporary stem (:data:`_PARK_STEM`) exists only inside
   that transaction.
5. Claim the written entries as in flight, release the locks, and remove the
   staging area.
6. Chunk and index what was written, one entry after another's assets but
   the entries concurrently, release the claims, and announce the commit once
   to the *owner* the caller names, with the workspaces it changed and the
   paths it moved and deleted.

It returns one report per item next to a
:class:`~hivegent.changes.WorkspaceChanged` with those workspaces and paths,
read off the resolved items under the locks, so the caller and the
announcement hold the same record.  A failure in
the last step leaves the new files in place without their chunks, which a null
``content_digest`` marks for the startup reconcile, and raises.
"""

import asyncio
import logging
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from functools import cache, partial
from itertools import chain
from pathlib import Path, PurePath, PurePosixPath
from types import MappingProxyType
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
    WorkspaceChanged,
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
    resolve_entry_paths,
    respell,
    stem_path_from_reference,
)
from ..files import disk_size, remove_path
from ..l10n import Localized
from ..llm_config import LlmConfig
from ..store import Casebase
from ..tools.base import PathFilter
from ..types import PipelineSpec
from ..workspace_events import announce_workspace_changed
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
from .operations import Location, Root, changed_root, route
from .paths import (
    _STAGE_PREFIX,
    DIRECTORY_PATH_REQUIRED,
    _enforce_file_size,
    _Journal,
    _parent_is_file,
    _shown,
    _write_workspace_file,
    directory_not_found,
    document_not_found,
)
from .prepare import _prepare_upload, _PreparedUpload, _Reserved

__all__ = [
    "AppliedChangeset",
    "Gateway",
    "PlannedChangeset",
    "apply_changeset",
    "plan_changeset",
]

logger = logging.getLogger(__name__)

_PARK_STEM = ".changeset-park"
"""The stem a moved unit's rows wait on between leaving and landing.

Never on disk: the rows pass through it inside the one transaction that moves
them, so no listing, index, or reconcile can meet it.
"""

_NO_FILTERS: Mapping[str, PathFilter] = MappingProxyType({})


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


def _crosses_roots(source: str, destination: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{source}' cannot move to '{destination}', since only one of them is "
            "written directly. Write its text to the destination and delete the source instead"
        ),
        de=(
            f"„{source}“ kann nicht nach „{destination}“ verschoben werden, da nur eines "
            "von beiden direkt geschrieben wird. Schreibe den Text ins Ziel und lösche die Quelle"
        ),
    )


def _kept(path: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' is reserved: an '.assets' folder belongs to its document, and "
            "a folder the host keeps can be read but not changed"
        ),
        de=(
            f"„{path}“ ist reserviert: Ein „.assets“-Ordner gehört zu seinem Dokument, "
            "und einen Ordner des Hosts kann man lesen, aber nicht ändern"
        ),
    )


def _carries_hidden(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' cannot move because it contains inaccessible entries",
        de=f"„{path}“ kann nicht verschoben werden, da es unzugängliche Einträge enthält",
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


def _depth(path: str | PurePath) -> int:
    return len(PurePosixPath(path).parts)


type _Key = tuple[str, str]


@dataclass(slots=True, frozen=True)
class _Unit:
    """What one item moves, deletes, or lands: an entry, or a whole tree.

    A tree is a directory, or a file of a root without a store, which has no entry.
    """

    root: Root
    path: str
    """The entry's stem, or the tree's root."""
    entry: EntryPaths | None = None

    @property
    def files(self) -> tuple[str, ...]:
        """The paths the unit consists of, everything below them included."""
        return (self.path,) if self.entry is None else self.entry.files

    def at(self, root: Root, path: str) -> Self:
        """The unit relocated to *path* in *root*, its companions renamed along."""
        entry = None if self.entry is None else self.entry.at(path)

        return replace(self, root=root, path=path, entry=entry)


type _Claims = dict[_Key, tuple[int, _Unit]]
"""Units by every path they consist of, with the item that claims each."""


@dataclass(slots=True, frozen=True)
class _Plan:
    """The units a changeset vacates and lands, and the paths it fills.

    One plan is one pass over roots that do not change meanwhile, so it
    caches what it reads of the disk: each root's case folding and each
    directory's listing.
    """

    filters: Mapping[str, PathFilter] = _NO_FILTERS
    """What the caller may see of each root, by its ``store_key``."""
    host: bool = False
    """Whether the host commits, which may change what a root reserves and evict to fit."""
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
    evicted: list[_Unit] = field(default_factory=list)
    """The files a root's quota evicts to fit the changeset."""
    folded: dict[str, bool] = field(default_factory=dict)
    listdir: Listdir = field(default_factory=lambda: cache(list_names))

    def key(self, root: Root, path: str) -> _Key:
        """What two spellings of one path share, compared as the root's filesystem does."""
        folded = self.folded.get(root.store_key)

        if folded is None:
            folded = self.folded[root.store_key] = folds_case(root.path)

        return root.store_key, path_key(path, folded=folded)

    def guard(self, root: Root, path: str, *, tree: bool = False) -> None:
        """Check *path*, or the *tree* below it, against the work in flight in a workspace."""
        if (store := root.store) is not None:
            check = _reject_if_scope_inflight if tree else _reject_if_inflight
            self.guards.append(partial(check, store, path))

    def check_reserved(self, root: Root, path: str) -> None:
        """Refuse a path *root* keeps from ordinary changes, which the host may make."""
        if not self.host and root.policy.reserves(root.path, path):
            raise HTTPException(status_code=400, detail=_kept(_shown(root, path)).current)

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

        root = location.root
        directory = root.path
        # Every path a change names passes here.
        self.check_reserved(root, str(path))

        if keep_name:
            spelled = PurePosixPath(respell(directory, str(path.parent), self.listdir), path.name)

            return Location(root, str(spelled))

        return Location(root, respell(directory, str(path), self.listdir))

    def holder(
        self, claims: _Claims, root: Root, path: str, skip: int | None = None
    ) -> tuple[int, _Unit] | None:
        """The deepest unit in *claims* holding *path*, ignoring the item *skip*."""
        pure = PurePosixPath(path)

        for candidate in (pure, *pure.parents):
            hit = claims.get(self.key(root, str(candidate))) if candidate.parts else None

            if hit is not None and hit[0] != skip:
                return hit

        return None

    def carry(self, unit: _Unit, path: str, to: _Unit) -> str | None:
        """Where *path* lies once *unit* is *to*, ``None`` if the unit does not hold it."""
        parts = PurePosixPath(path).parts

        for old, new in zip(unit.files, to.files, strict=True):
            held = str(PurePosixPath(*parts[: _depth(old)]))

            if self.key(unit.root, held) == self.key(unit.root, old):
                return rebase(path, held, new)

        return None

    def claim(self, claims: _Claims, index: int, unit: _Unit) -> None:
        for path in unit.files:
            key = self.key(unit.root, path)

            if key in claims and claims[key][0] != index:
                raise HTTPException(
                    status_code=400, detail=_claimed_twice(_shown(unit.root, path)).current
                )

            claims[key] = (index, unit)

    def fill(self, index: int, target: Location) -> None:
        key = self.key(target.root, target.path)

        if self.filled.setdefault(key, index) != index:
            raise HTTPException(
                status_code=400, detail=_claimed_twice(target.canonical).current
            )

    def claim_write(self, index: int, unit: _Unit) -> None:
        """Claim the files a text item writes, an original's projection included.

        They share one folder and one stem, so both are checked once.
        """
        root, first = unit.root, unit.files[0]
        self.check_parents(root, first)
        self.guard(root, first)

        for path in unit.files:
            target = Location(root, path)
            self.fill(index, target)
            self.written[self.key(root, path)] = target

    def origin(
        self, root: Root, path: str, skip: int | None = None
    ) -> Location | None:
        """The current path whose content lies at *path* once the changeset applied.

        ``None`` when nothing of the current workspace ends up there.  *skip*
        ignores one item's own landing, to ask what else would be there.
        """
        landing = self.holder(self.landed, root, path, skip)

        if landing is not None:
            index, destination = landing
            source = self.moves[index][0]
            initial = self.carry(destination, path, source)

            if initial is None:
                return None

            owner = self.holder(self.vacated, source.root, initial)

            return Location(source.root, initial) if owner and owner[0] == index else None

        return None if self.holder(self.vacated, root, path) else Location(root, path)

    def occupied(self, root: Root, path: str, skip: int | None = None) -> Path | None:
        """What is on disk now and ends up at *path*, besides the landing of *skip*."""
        origin = self.origin(root, path, skip)
        full = None if origin is None else origin.full_path

        return full if full is not None and full.exists() else None

    def check_parents(self, root: Root, path: str) -> None:
        """Refuse a final path below one that is a file once the changeset applied."""
        for parent in PurePosixPath(path).parents:
            occupant = self.occupied(root, str(parent)) if parent.parts else None

            if occupant is not None and occupant.is_file():
                raise HTTPException(
                    status_code=409,
                    detail=_parent_is_file(_shown(root, str(parent))).current,
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

    def _final_names(self, root: Root, directory: str) -> set[str]:
        """Every name *directory* holds once the changeset applied.

        What the disk holds there now or where it comes from, and what lands or
        is written into it.
        """
        source = self.origin(root, directory)
        parent = self.key(root, directory)
        arriving = (
            *(
                Location(unit.root, path)
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
                if other.root == root
                and self.key(root, str(PurePosixPath(other.path).parent)) == parent
            ),
        }

    def check_slot(self, unit: _Unit) -> None:
        """Refuse a new original whose stem holds another entry once the changeset applied.

        An upload's slot check, asked of the final state like every other check
        here: a stem the changeset vacates is free, and one it moves an entry
        onto, or writes another part of, is not.
        """
        root, stem = unit.root, PurePosixPath(unit.path)

        for path in unit.files:
            occupant = self.occupied(root, path)

            if occupant is not None:
                detail = (
                    _is_existing_directory(_shown(root, path))
                    if occupant.is_dir()
                    else _DOCUMENT_EXISTS
                )
                raise HTTPException(status_code=409, detail=detail.current)

        own = {self.key(root, path) for path in unit.files}
        name = self.key(root, stem.name)

        for sibling_name in self._final_names(root, str(stem.parent)):
            sibling = str(stem.parent / sibling_name)
            key = self.key(root, sibling)

            if (
                key not in own
                and self.key(root, PurePosixPath(sibling_name).stem) == name
                and (key in self.written or self.occupied(root, sibling))
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
    def store(self) -> Casebase | None:
        """The casebase whose rows the written file carries, ``None`` for a plain file."""
        return self.target.root.store

    @property
    def is_original(self) -> bool:
        """Whether the write lands on a workspace original whose projection is re-derived."""
        return self.store is not None and not is_description_file(self.target.path)


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

    @property
    def indexed(self) -> bool:
        """Whether the item changes a workspace, which is approved, indexed, and announced."""
        return changed_root(self.operation).store is not None


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
    root, path = location.root, location.path
    full = location.full_path
    _check_basis(_shown(root, path), full, basis)
    is_dir = full.is_dir()

    if expect is not None and expect != ("dir" if is_dir else "entry"):
        raise HTTPException(
            status_code=404, detail=_NOT_FOUND[expect](_shown(root, path)).current
        )

    # A plain file has no row to stand in for it, so it must be on disk.
    if root.store is None and not os.path.lexists(full):
        raise HTTPException(
            status_code=404, detail=document_not_found(_shown(root, path)).current
        )

    # A directory move carries everything below it and changes the paths the
    # filter matches, so the filter's own entries are asked, not the tree, and
    # the refusal names only the directory, keeping hidden paths private.
    hidden = plan.filters.get(root.store_key)

    if moving and is_dir and hidden is not None and hidden.hides_within(path):
        raise HTTPException(
            status_code=403, detail=_carries_hidden(_shown(root, path)).current
        )

    plan.guard(root, path, tree=is_dir)

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

    root, path = destination.root, destination.path
    same_root = root == source.root

    # Only rows carry a file between roots, so nothing moves to or from a plain one.
    if not same_root and None in (root.store, source.root.store):
        raise HTTPException(
            status_code=400,
            detail=_crosses_roots(operation.source.canonical, destination.canonical).current,
        )

    named = source.entry.description_path if source.entry else source.path

    if not path or (
        destination.full_path.is_dir() and plan.holder(plan.vacated, root, path) is None
    ):
        directory = respell(root.path, path, plan.listdir)
        path = str(PurePosixPath(directory, PurePosixPath(named).name))

    if source.entry is None:
        if same_root and is_below(plan.key(root, path)[1], plan.key(root, source.path)[1]):
            raise HTTPException(
                status_code=400, detail=_into_itself(_shown(source.root, source.path)).current
            )

        plan.check_reserved(root, path)
        plan.guard(root, path, tree=True)
    else:
        path = stem_path_from_reference(path)
        plan.check_reserved(root, path)
        plan.guard(root, description_path_for_stem(path))

    if same_root and path == source.path:
        raise HTTPException(
            status_code=400, detail=_same_paths(_shown(root, path)).current
        )

    target = source.at(root, path)
    plan.moves[index] = source, target
    plan.claim(plan.landed, index, target)
    # The destination named is the counterpart of the path the source was
    # addressed by, so a move of an original reads back as one.
    moved_to = plan.carry(source, operation.source.path, target) or target.files[0]

    return replace(operation, destination=Location(root, moved_to))


def _check_landing(index: int, destination: _Unit, plan: _Plan) -> None:
    paths = set(destination.files)
    origin = plan.origin(destination.root, destination.files[0], skip=index)

    if destination.entry is not None and origin is not None:
        paths.update(
            resolve_entry_paths(
                origin.root.path, origin.path, plan.listdir
            ).at(destination.path).files
        )

    for path in sorted(paths):
        if plan.occupied(destination.root, path, skip=index) is not None:
            raise HTTPException(
                status_code=409,
                detail=_destination_exists_at(_shown(destination.root, path)).current,
            )

        plan.check_parents(destination.root, path)


def _resolve_text(operation: Write[Location] | Edit[Location], index: int, plan: _Plan) -> _Resolved:
    target = plan.sanitized(operation.target)
    root, path = target.root, target.path
    shown = _shown(root, path)
    # A file a move carries here is read where it lies until the move lands it.
    origin = plan.origin(root, path)
    mutate = (
        write_mutation(shown, operation.content, operation.mode)
        if isinstance(operation, Write)
        else edit_mutation(shown, operation.edits)
    )
    current, content, report = derive_text(
        None if origin is None else origin.full_path, shown, mutate, operation.basis
    )
    chunking = operation.chunking if isinstance(operation, Write) else None
    effect = _TextEffect(target, current, content, chunking, origin)
    resolved = replace(operation, target=target)

    # A plain file is bounded by its root's quota alone.
    if effect.store is None:
        plan.claim_write(index, _Unit(root, path))

        return _Resolved(resolved, effect, report)

    _enforce_file_size(content.encode("utf-8"))
    stem = stem_path_from_reference(path)
    unit = _Unit(root, stem, EntryPaths(stem, path, None, None))

    if effect.is_original:
        if current is None and not writes_as_text(path):
            raise HTTPException(status_code=400, detail=_binary_write(shown).current)

        # An original's write regenerates its projection, so the item fills both.
        unit = _Unit(root, stem, EntryPaths(stem, description_path_for_stem(stem), path, None))

        # Creating an original claims the whole stem: requiring a free one keeps
        # the write from superseding another entry's description or original.
        if current is None:
            plan.originals.append(unit)

        report = _regenerated(report, _shown(root, unit.files[0])).current

    plan.claim_write(index, unit)

    return _Resolved(resolved, effect, report)


def _resolve_directory(operation: CreateDir[Location], index: int, plan: _Plan) -> _Resolved:
    if not operation.target.path:
        raise HTTPException(status_code=400, detail=DIRECTORY_PATH_REQUIRED.current)

    target = plan.sanitized(operation.target)
    root, path = target.root, target.path
    plan.guard(root, path)

    if plan.occupied(root, path) is not None:
        raise HTTPException(
            status_code=409, detail=_already_exists(target.canonical).current
        )

    plan.check_parents(root, path)
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
    scopes: tuple[str, ...],
) -> tuple[list[_Resolved], WorkspaceChanged]:
    """Resolve every item from its located source and rows, sources first.

    Returns the items with how they change the workspaces of *scopes*, the
    paths they move and delete read off the disk in the same pass, before
    anything moves.
    """
    units: dict[int, _Unit] = {}

    for index, (location, is_dir) in sources.items():
        root, path = location.root, location.path

        if (store := root.store) is not None and not is_dir:
            row = rows.get((store, stem_path_from_reference(path)))
            entry = _entry_paths(store, path, row)
            units[index] = _Unit(root, entry.stem_path, entry)
        else:
            units[index] = _Unit(root, path)

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

    _admit(plan, resolved)
    indexed = any(item.indexed for item in resolved)

    changed = _workspace_changed(resolved, plan, scopes) if indexed else WorkspaceChanged(scopes)

    return resolved, changed


def _admit(plan: _Plan, resolved: Sequence[_Resolved]) -> None:
    """Put what the changeset adds to each root with a quota to it, noting the files it evicts.

    A write adds its text less what it replaces, a delete takes away its tree
    (once, however many of its parts are deleted too), and a move adds nothing.
    No file the changeset writes or vacates is evicted.
    """
    growth: Counter[Root] = Counter()
    keep: set[Path] = set()
    removed = [
        item.effect.unit
        for item in resolved
        if isinstance(item.effect, _Vacate) and item.effect.destination is None
    ]

    for item in resolved:
        match item.effect:
            case _TextEffect(target=target, content=content, origin=origin) if (
                target.root.quota is not None
            ):
                root = target.root
                replaced = 0 if origin is None else disk_size(origin.full_path)
                growth[root] += len(content.encode("utf-8")) - replaced
                keep.add(target.full_path)
            case _Vacate(unit=unit, destination=destination) if unit.root.quota is not None:
                root, full = unit.root, unit.root.path / unit.path
                keep.add(full)
                counted = destination is None and not any(
                    other.root == root and is_below(unit.path, other.path) for other in removed
                )
                growth[root] -= disk_size(full) if counted else 0
            case _:
                pass

    for root, delta in growth.items():
        if (quota := root.quota) is not None:
            evicted = quota.admit(root.path, root.scope.render(""), delta, keep, evict=plan.host)
            plan.evicted.extend(
                _Unit(root, path.relative_to(root.path).as_posix()) for path in evicted
            )


async def _resolve_all(
    changeset: Changeset[Location], filters: Mapping[str, PathFilter], *, host: bool
) -> tuple[list[_Resolved], _Plan, WorkspaceChanged]:
    """Resolve every item: the disk in worker threads, the rows in between in one query."""
    plan = _Plan(filters=filters, host=host)
    scopes = tuple(
        sorted(
            {
                store.scope.prefix
                for location in changeset.locations
                if (store := location.root.store) is not None
            }
        )
    )
    operations = list(changeset.operations)
    sources = await asyncio.to_thread(_locate_sources, plan, operations)
    plan.check_inflight()
    rows = await db_documents.get_entries_metadata(
        (store, location.path)
        for location, is_dir in sources.values()
        if (store := location.root.store) is not None and not is_dir
    )
    resolved, changed = await asyncio.to_thread(
        _settle, plan, operations, sources, rows, scopes
    )
    plan.check_inflight()

    return resolved, plan, changed


def _shown_files(unit: _Unit) -> tuple[str, ...]:
    """The paths of *unit* a person sees go: a tree's root, an entry's files on disk.

    An entry's description and original, or its description alone when only
    its row is left of it.
    """
    if (entry := unit.entry) is None:
        return (unit.path,)

    root = unit.root.path
    present = (entry.description_path, entry.original_path)

    return tuple(
        path for path in present if path is not None and (root / path).exists()
    ) or (entry.description_path,)


def _workspace_changed(
    resolved: Sequence[_Resolved], plan: _Plan, scopes: tuple[str, ...]
) -> WorkspaceChanged:
    """How *resolved* changes *scopes*, what it moves and deletes named before anything changed.

    Blocking.  What an approval shows and what a client follows, so the files
    of a root without a store are left out of both.
    """
    moves: list[PathMove] = []
    deletes: list[str] = []

    def replaces(root: Root, path: str) -> bool:
        """Whether a file there now goes, deleted by another item."""
        hit = plan.holder(plan.vacated, root, path)
        effect = None if hit is None else resolved[hit[0]].effect

        return (
            isinstance(effect, _Vacate)
            and effect.destination is None
            and Location(root, path).full_path.exists()
        )

    for item in resolved:
        match item.effect:
            case _ if not item.indexed:
                pass
            case _Vacate(unit=unit, destination=None):
                deletes.extend(_shown(unit.root, path) for path in _shown_files(unit))
            case _Vacate(unit=unit, destination=_Unit() as destination):
                moved = dict(zip(unit.files, destination.files, strict=True))
                moves.extend(
                    PathMove(
                        _shown(unit.root, path),
                        _shown(destination.root, moved[path]),
                        is_dir=unit.entry is None,
                        replaces=replaces(destination.root, moved[path]),
                    )
                    for path in _shown_files(unit)
                )
            case _:
                pass

    return WorkspaceChanged(scopes, tuple(moves), tuple(deletes))


def _summarize(resolved: Sequence[_Resolved], changed: WorkspaceChanged) -> ChangesetSummary:
    """What *resolved* does as a person approving it reads it, moving and deleting as *changed* says.

    Only a workspace change is put to a person, so only one is shown.
    """
    creates: list[FileDiff] = []
    updates: list[FileDiff] = []
    mkdirs: list[str] = []
    budget = MAX_SUMMARY_DIFF_CHARS

    for item in resolved:
        match item.effect:
            case _ if not item.indexed:
                pass
            case _TextEffect(target=target, current=current, content=content):
                shown = target.canonical
                diff = capped_diff(shown, current, content, min(budget, MAX_DIFF_CHARS))
                budget -= len(diff)
                (creates if current is None else updates).append(FileDiff(shown, diff))
            case _MakeDir(target=target):
                mkdirs.append(target.canonical)
            case _Vacate():
                pass

    return ChangesetSummary(
        tuple(creates), tuple(updates), changed.moves, changed.deletes, tuple(mkdirs)
    )


@dataclass(slots=True, frozen=True)
class PlannedChangeset:
    """A changeset the gateway would commit, and what approving it shows.

    Attributes:
        changeset: The operations with sanitized paths and every move
            destination resolved to the path it lands on, ready to be stored
            and applied.
        summary: The creates and updates with their capped diffs, the moves
            of every file and directory and the deletes with every companion
            they take along, and the new directories.
    """

    changeset: Changeset[Location]
    summary: ChangesetSummary


@dataclass(slots=True, frozen=True)
class AppliedChangeset:
    """What the gateway committed.

    Attributes:
        reports: One human-readable report per operation, in order, for the
            tool or person that asked.
        changed: The workspaces it changed and the paths it moved and
            deleted, as resolved, for every client to refresh and follow.
            The same record the commit announces.
    """

    reports: tuple[str, ...]
    changed: WorkspaceChanged


async def plan_changeset(
    changeset: Changeset[Location],
    *,
    filters: Mapping[str, PathFilter] = _NO_FILTERS,
    host: bool = False,
) -> PlannedChangeset:
    """Resolve and validate *changeset* without changing anything.

    Args:
        changeset: The operations to check, as a caller spelled them.
        filters: What the caller may see of each root, by its ``store_key``.
        host: Whether the host asks, which may change what a root reserves.

    Returns:
        The resolved changeset and its summary.

    Raises:
        HTTPException: When an item is invalid on its own (a missing source,
            an occupied destination, a reserved path, a stale basis, a
            directory carrying hidden entries) or claims a path another item
            claims.
    """
    resolved, _plan, changed = await _resolve_all(changeset, filters, host=host)

    return PlannedChangeset(
        Changeset(tuple(item.operation for item in resolved)),
        await asyncio.to_thread(_summarize, resolved, changed),
    )


# ─── Application ──────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class _PreparedOriginal:
    """A text original's regenerated projection, converted ahead of the lock from *effect*."""

    store: Casebase
    effect: _TextEffect
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


async def _prepare_original(original: tuple[Casebase, _TextEffect]) -> _PreparedOriginal:
    """Run a text original's new bytes through the upload conversion.

    The same pipeline as an upload of the edited file, so the projection left
    behind is byte for byte the one uploading it would produce, and a failed
    conversion leaves the previous entry untouched.
    """
    store, effect = original
    path = effect.target.path
    data = effect.content.encode("utf-8")
    # A carried original keeps the rows it has until the move lands them here.
    origin = effect.origin
    metadata = (
        await db_documents.get_entry_metadata(holder, origin.path)
        if origin is not None and (holder := origin.root.store) is not None
        else None
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

    return _PreparedOriginal(store, effect, reserved, upload)


type _RowChange = Callable[[AsyncSession], Awaitable[object]]
"""One row write, run inside the changeset's single transaction."""


def _install_text(
    effect: _TextEffect,
    journal: _Journal,
    staging: Path,
    prepared: _PreparedOriginal | None,
) -> list[_Indexing]:
    """Install a text item's files, returning one entry's projections in index order.

    A plain file is installed as it is and indexed nowhere.
    """
    if prepared is not None:
        workspace = prepared.store.path

        for change in _stage_prepared(staging, prepared.upload, prepared.reserved):
            journal.apply(workspace, change)

        return [
            _Indexing(
                prepared.store,
                written.entry.description_path,
                written.entry.markdown,
                effect.chunking,
                written.stat,
                written.entry.entry_metadata,
            )
            for written in _written_entries(workspace, prepared.upload)
        ]

    target, store = effect.target, effect.store
    live = target.full_path
    journal.install(
        _write_workspace_file(staging, target.path, effect.content.encode("utf-8")), live
    )

    if store is None:
        return []

    stat = ContentStat.from_path(live)

    return [_Indexing(store, target.path, effect.content, effect.chunking, stat)]


def _text_rows(effect: _TextEffect, prepared: _PreparedOriginal | None) -> list[_RowChange]:
    """Drop the asset rows of a rewritten original, whose new projection supersedes them."""
    if prepared is None or not prepared.reserved.preserve:
        return []

    store = prepared.store
    assets = assets_dir_for_stem(stem_path_from_reference(effect.target.path))

    return [lambda s: db_documents.delete_subtree(store, assets, s=s)]


def _move_rows(source: _Unit, destination: _Unit) -> list[_RowChange]:
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

    changes: list[_RowChange] = [
        lambda s: db_documents.move_document(
            origin, source.path, target, destination.path, s=s
        )
    ]
    old, new = source.entry.assets_dir, assets_dir_for_stem(destination.path)

    if old is not None:
        changes.append(
            lambda s: db_documents.move_subtree(origin, old, target, new, s=s)
        )

    return changes


type _Park = tuple[Path, _Unit]
"""Where a moved unit waits between leaving and landing: its files' directory, its rows' stem."""


def _leave(effect: _Vacate, journal: _Journal, park: _Park) -> list[_RowChange]:
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


def _remove(unit: _Unit, journal: _Journal) -> list[_RowChange]:
    """Park a deleted unit's files for good, returning its rows to drop.

    A plain file has no rows, an entry its own, and a tree all those below its path.
    """
    workspace, casebase, entry = unit.root.path, unit.root.store, unit.entry

    if entry is None:
        journal.remove(workspace / unit.path)
    else:
        _remove_entry_files(journal, workspace, entry)

    if casebase is None:
        return []

    if entry is not None:
        return [lambda s: _remove_entry_rows(s, casebase, entry)]

    return [lambda s: db_documents.delete_subtree(casebase, unit.path, s=s)]


def _land(
    unit: _Unit, destination: _Unit, journal: _Journal, park: _Park, staging: Path
) -> list[_RowChange]:
    """Move a parked unit's files and rows to *destination*.

    The description addresses its payload as ``<stem>.assets/...``, so its
    references follow a changed basename.
    """
    parked, stem = park
    workspace = destination.root.path
    names = PurePosixPath(unit.path).name, PurePosixPath(destination.path).name
    entry = unit.entry
    description = entry.description_path if entry and entry.assets_dir else None

    for old, new in zip(unit.files, destination.files, strict=True):
        held = parked / PurePosixPath(old).name

        if not os.path.lexists(held):
            continue

        if old == description and names[0] != names[1]:
            text = _decode_existing(held, _shown(unit.root, old)).text
            staged = _write_workspace_file(
                staging, new, repoint_asset_refs(text, *names).encode("utf-8")
            )
            journal.install(staged, workspace / new)
        else:
            journal.rename(held, workspace / new)

    return _move_rows(stem, destination)


def _install_files(
    resolved: Sequence[_Resolved],
    evicted: Iterable[_Unit],
    prepared: Mapping[int, _PreparedOriginal],
    staging: Path,
    journal: _Journal,
) -> tuple[list[list[_Indexing]], list[_RowChange]]:
    """Land every item's files, returning the projections to index and the row changes to make.

    Blocking, and the caller's journal restores the files on failure.  The
    files a quota evicts leave with the deletes.
    """
    vacates = (
        *(
            (index, item.effect)
            for index, item in enumerate(resolved)
            if isinstance(item.effect, _Vacate)
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
    rows: list[_RowChange] = []
    pending: list[list[_Indexing]] = []

    for index, effect in vacating:
        unit = effect.unit
        parks[index] = staging / "park" / str(index), unit.at(unit.root, f"{token}/{index}")
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
    prepared: Mapping[int, _PreparedOriginal],
    owner: str | None,
    exclude_client: str | None,
    filters: Mapping[str, PathFilter],
    host: bool,
) -> AppliedChangeset:
    roots = [location.root for location in changeset.locations]
    staging = _staging_root(roots)
    journal = _Journal(staging / "backup")

    with ExitStack() as claims:
        try:
            async with _locked(*roots):
                resolved, plan, changed = await _resolve_all(changeset, filters, host=host)

                for index, original in prepared.items():
                    if resolved[index].effect != original.effect:
                        shown = original.effect.target.canonical
                        raise HTTPException(
                            status_code=409, detail=_changed_meanwhile(shown).current
                        )

                try:
                    pending, rows = await asyncio.to_thread(
                        _install_files, resolved, plan.evicted, prepared, staging / "new", journal
                    )

                    if rows:
                        async with db_engine.session() as s:
                            for change in rows:
                                await change(s)

                except BaseException:
                    await asyncio.to_thread(journal.rollback)
                    raise

                # Until its chunks land, a written entry stays hidden from
                # inventory reads and refuses every other mutation.
                for item in chain.from_iterable(pending):
                    claims.callback(_discard_inflight, item.store, item.description_path)
                    _add_inflight(item.store, item.description_path)

        finally:
            await asyncio.to_thread(remove_path, staging)

        try:
            await _index_all(pending)
        finally:
            if owner is not None:
                announce_workspace_changed(owner, changed, exclude_client=exclude_client)

    return AppliedChangeset(tuple(item.report for item in resolved), changed)


async def apply_changeset(
    changeset: Changeset[Location],
    *,
    owner: str | None = None,
    exclude_client: str | None = None,
    filters: Mapping[str, PathFilter] = _NO_FILTERS,
    host: bool = False,
) -> AppliedChangeset:
    """Commit *changeset* all or nothing, see the module docstring for the phases.

    Args:
        changeset: The operations to apply, planned or as a caller spelled them.
        owner: The user whose clients hear about the change, or nobody when
            ``None``.  A group workspace's casebase names the group, not the
            user to tell, which is why the caller says.
        exclude_client: The client that asked, which learns what changed from
            the result rather than from the announcement.
        filters: What the caller may see of each root, by its ``store_key``.
        host: Whether the host commits, which may change what a root reserves
            and evict what its quota lets go to fit.

    Returns:
        The reports and how the workspaces changed.

    Raises:
        HTTPException: When the changeset is invalid (see
            :func:`plan_changeset`), or a text original's file changed while
            it was being prepared.
    """
    if not changeset.operations:
        return AppliedChangeset((), WorkspaceChanged(()))

    prepared: dict[int, _PreparedOriginal] = {}

    # Only a text written in a workspace may be an original to convert ahead of the locks.
    if any(
        isinstance(op, Write | Edit) and op.target.root.store is not None
        for op in changeset.operations
    ):
        planned, _plan, _changed = await _resolve_all(changeset, _NO_FILTERS, host=host)
        originals = {
            index: (store, effect)
            for index, item in enumerate(planned)
            if isinstance(effect := item.effect, _TextEffect)
            and effect.is_original
            and (store := effect.store) is not None
        }
        converted = await bounded_gather(
            originals.values(), _prepare_original, limit=settings.jobs.collection_concurrency
        )
        prepared = dict(zip(originals, converted, strict=True))

    # Shielded as one unit even though it releases the locks partway through,
    # so a cancel can never settle between the file swap and the index and
    # leave new markdown wearing its predecessor's rows.
    return await shield_to_completion(
        _commit(changeset, prepared, owner, exclude_client, filters, host)
    )


@dataclass(slots=True, frozen=True)
class Gateway:
    """The gateway bound to the roots a caller may change, what it may see, and the user it tells.

    The one place a surface's canonical paths meet the gateway, so the agent,
    the MCP surface, and the host saving a tool result supply only their roots,
    filters and the user they tell.

    No client is excluded from the announcement, since none of these surfaces
    answers a client that could follow the result itself: the tab whose chat
    runs the agent learns of its changes through the feed like every other.

    Attributes:
        roots: The roots a canonical path may route to.
        owner: The user whose clients hear about an applied change.
        filters: What the caller may see of each root, by its ``store_key``.
        host: Whether the host commits, see :func:`apply_changeset`.
    """

    roots: tuple[Root, ...]
    owner: str | None = None
    filters: Mapping[str, PathFilter] = field(default_factory=dict)
    host: bool = False

    def gated(self, changeset: Changeset[str]) -> tuple[bool, ...]:
        """Whether each item of *changeset* changes a workspace, which a person approves.

        Raises:
            ValueError: When a path names none of :attr:`roots`.
        """
        routed = route(changeset, self.roots)

        return tuple(changed_root(op).store is not None for op in routed.operations)

    async def plan(self, changeset: Changeset[str]) -> PlannedChangeset:
        """Route and plan *changeset*, see :func:`plan_changeset`.

        Raises:
            ValueError: When a path names none of :attr:`roots`.
            HTTPException: When the gateway refuses the changeset.
        """
        routed = route(changeset, self.roots)

        return await plan_changeset(routed, filters=self.filters, host=self.host)

    async def apply(self, changeset: Changeset[str]) -> AppliedChangeset:
        """Route and apply *changeset*, see :func:`apply_changeset`.

        Raises:
            ValueError: When a path names none of :attr:`roots`.
            HTTPException: When the gateway refuses the changeset.
        """
        return await apply_changeset(
            route(changeset, self.roots), owner=self.owner, filters=self.filters, host=self.host
        )

    async def commit(self, changeset: Changeset[str]) -> str:
        """Apply *changeset* as :meth:`apply` does, its reports joined into one receipt."""
        return "\n".join((await self.apply(changeset)).reports)
