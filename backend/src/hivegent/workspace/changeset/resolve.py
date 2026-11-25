"""Planning a changeset, resolving and validating it without touching anything.

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
the resolved changeset with the :class:`~hivegent.tools.changes.ChangesetSummary` a
person approving it reads, which leaves a folder's items out, since only a
workspace change is put to a person.
"""

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path

from ...db import documents as db_documents
from ...files import disk_size
from ...metadata import EntryMetadata
from ...shared.formats import writes_as_text
from ...store import Workspace
from ...tools.changes import (
    ChangeRefused,
    Changeset,
    ChangesetSummary,
    CreateDir,
    Delete,
    Edit,
    Move,
    WorkspaceChanged,
    Write,
)
from ...tools.entries import (
    CASE_BASE_KINDS,
    ContentStat,
    EntryPaths,
    Part,
    entry_exists,
    is_below,
    is_description_file,
    resolve_entry_paths,
    stem_path_from_reference,
)
from ..documents import (
    _binary_write,
    _check_basis,
    _regenerated,
    derive_text,
    edit_mutation,
    write_mutation,
)
from ..metadata import _entry_paths, _merge_entry_paths
from ..operations import Item, Location, Root, Upload, locations
from ..paths import DIRECTORY_PATH_REQUIRED, _enforce_file_size, _shown, already_exists
from .model import (
    _MakeDir,
    _Resolved,
    _TextEffect,
    _Unit,
    _UploadEffect,
    _Vacate,
)
from .plan import _check_landing, _landing, _locate_sources, _Plan, _Sources
from .report import _summarize, _workspace_changed
from .rules import (
    _created_directory,
    _deleted,
    _kept_by_cbrkit,
    _moved,
    _sidecar_follows,
    _uploaded,
)

__all__ = [
    "PlannedChangeset",
    "plan_changeset",
]


def _resolve_text(
    operation: Write[Location] | Edit[Location], index: int, plan: _Plan
) -> _Resolved:
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
    effect = _TextEffect(target, current, content, content.encode("utf-8"), origin)
    resolved = replace(operation, target=target)

    # A plain file is bounded by its root's quota alone.
    if effect.store is None:
        plan.claim_write(index, _Unit.of_tree(root, path))

        return _Resolved(resolved, effect, report)

    _enforce_file_size(effect.data)
    if Part.of(path).kind in CASE_BASE_KINDS:
        raise ChangeRefused(status_code=400, detail=_kept_by_cbrkit(shown).current)

    stem = stem_path_from_reference(path)
    unit = plan.description(target) or _Unit(root, EntryPaths.entry(stem))

    if effect.is_original:
        if current is None and not writes_as_text(path):
            raise ChangeRefused(status_code=400, detail=_binary_write(shown).current)

        # An original's write regenerates its projection, so the item fills both.
        unit = _Unit(root, EntryPaths.entry(stem, path))

        # Creating an original claims the whole stem: requiring a free one keeps
        # the write from superseding another entry's description or original.
        if current is None:
            plan.originals.append(unit)

        report = _regenerated(report, _shown(root, unit.files[0])).current

    plan.claim_write(index, unit)

    return _Resolved(resolved, effect, report)


def _resolve_directory(
    operation: CreateDir[Location], index: int, plan: _Plan
) -> _Resolved:
    if not operation.target.path:
        raise ChangeRefused(status_code=400, detail=DIRECTORY_PATH_REQUIRED.current)

    target = plan.sanitized(operation.target)
    root, path = target.root, target.path

    if plan.occupied(root, path) is not None:
        raise ChangeRefused(
            status_code=409, detail=already_exists(target.canonical).current
        )

    plan.check_parents(root, path)
    plan.fill(index, target)

    return _Resolved(
        replace(operation, target=target),
        _MakeDir(target),
        _created_directory(target.canonical).current,
    )


def _resolve_upload(
    operation: Upload,
    index: int,
    plan: _Plan,
    rows: Mapping[tuple[Workspace, str], EntryMetadata],
) -> _Resolved:
    """Resolve an upload from its located target, and the row of the entry it overwrites."""
    target = operation.target
    root, path = target.root, target.path

    if (store := root.store) is None:
        raise ValueError("An upload lands in a workspace")

    if Part.of(path).kind in CASE_BASE_KINDS:
        raise ChangeRefused(
            status_code=400, detail=_sidecar_follows(target.canonical).current
        )

    _check_basis(target.canonical, target.full_path, operation.basis)
    _enforce_file_size(operation.content)
    stem = stem_path_from_reference(path)
    markdown = is_description_file(path)
    original = None if markdown else (path, operation.content)

    if operation.companion is not None:
        companion, data = operation.companion
        _enforce_file_size(data)
        original = plan.sanitized(Location(root, companion)).path, data

    unit = _Unit(
        root, EntryPaths.entry(stem, None if original is None else original[0])
    )
    row = rows.get((store, stem)) if operation.overwrite else None
    overwrite, superseded = operation.overwrite, None

    if overwrite:
        existing = _merge_entry_paths(
            resolve_entry_paths(root.path, path, plan.listdir), row
        )

        # The upload keeps the assets and cbrkit companions of an original it replaces in place.
        kept = replace(
            unit.paths,
            kinds=unit.paths.kinds | existing.kinds - {"description", "original"},
        )

        if set(existing.files) - set(kept.files):
            superseded = _Unit(root, existing)
            plan.claim(plan.vacated, index, superseded)
            overwrite = False

    plan.claim_write(index, unit)
    (plan.overwrites if overwrite else plan.originals).append(unit)
    present = tuple(ContentStat.from_path(root.path / file) for file in unit.files)
    replacing = overwrite and entry_exists(root.path, path)
    origin = operation.origin if row is None else row.origin
    effect = _UploadEffect(
        operation, target, original, origin, present, replacing, superseded
    )

    return _Resolved(operation, effect, _uploaded(target.canonical).current)


def _settle(
    plan: _Plan,
    operations: list[Item],
    sources: _Sources,
    rows: Mapping[tuple[Workspace, str], EntryMetadata],
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
        # A move addresses an asset's entry by its description, as any other entry's.
        moving = isinstance(operations[index], Move)
        described = None if is_dir or moving else plan.description(location)

        if described is not None:
            units[index] = described
        elif root.store is not None and Part.of(path).kind in CASE_BASE_KINDS:
            if moving:
                raise ChangeRefused(
                    status_code=400, detail=_sidecar_follows(location.canonical).current
                )

            units[index] = _Unit(root, EntryPaths.tree(path), tree=True)
        elif (store := root.store) is not None and not is_dir:
            row = rows.get((store, stem_path_from_reference(path)))
            units[index] = _Unit(root, _entry_paths(store, path, row))
        else:
            units[index] = _Unit.of_tree(root, path)

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
                resolved.append(
                    _Resolved(op, effect, _deleted(target.canonical).current)
                )
            case Write() | Edit():
                resolved.append(_resolve_text(op, index, plan))
            case CreateDir():
                resolved.append(_resolve_directory(op, index, plan))
            case Upload():
                resolved.append(_resolve_upload(op, index, plan, rows))

    plan.check_written_parents()
    plan.check_described()

    for unit in plan.originals:
        plan.check_slot(unit)

    for unit in plan.overwrites:
        plan.check_slot(unit, overwrite=True)

    _admit(plan, resolved)
    indexed = any(item.indexed for item in resolved)

    changed = (
        _workspace_changed(resolved, plan, scopes, lambda item: item.indexed)
        if indexed
        else WorkspaceChanged(scopes)
    )

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
            case _TextEffect(target=target, data=data, origin=origin) if (
                target.root.quota is not None
            ):
                root = target.root
                replaced = 0 if origin is None else disk_size(origin.full_path)
                growth[root] += len(data) - replaced
                keep.add(target.full_path)
            case _Vacate(unit=unit, destination=destination) if (
                unit.root.quota is not None
            ):
                root, full = unit.root, unit.root.path / unit.path
                keep.add(full)
                counted = destination is None and not any(
                    other.root == root and is_below(unit.path, other.path)
                    for other in removed
                )
                growth[root] -= disk_size(full) if counted else 0
            case _:
                pass

    for root, delta in growth.items():
        if (quota := root.quota) is not None:
            evicted = quota.admit(
                root.path, root.mount.target, delta, keep, evict=plan.host
            )
            plan.evicted.extend(
                _Unit.of_tree(root, path.relative_to(root.path).as_posix())
                for path in evicted
            )


async def _resolve_all(
    items: Sequence[Item], *, host: bool
) -> tuple[list[_Resolved], _Plan, WorkspaceChanged]:
    """Resolve every item: the disk in worker threads, the rows in between in one query."""
    plan = _Plan(host=host)
    scopes = tuple(
        sorted(
            {
                store.scope.prefix
                for location in locations(items)
                if (store := location.root.store) is not None
            }
        )
    )
    operations = list(items)
    sources = await asyncio.to_thread(_locate_sources, plan, operations)
    overwritten = (
        op.target for op in operations if isinstance(op, Upload) and op.overwrite
    )
    rows = await db_documents.get_entries_metadata(
        (store, location.path)
        for location in chain(
            (location for location, is_dir in sources.values() if not is_dir),
            overwritten,
        )
        if (store := location.root.store) is not None
    )
    resolved, changed = await asyncio.to_thread(
        _settle, plan, operations, sources, rows, scopes
    )

    return resolved, plan, changed


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


async def plan_changeset(
    changeset: Changeset[Location],
    *,
    host: bool = False,
) -> PlannedChangeset:
    """Resolve and validate *changeset* without changing anything.

    Args:
        changeset: The operations to check, as a caller spelled them.
        host: Whether the host asks, which may change what a root reserves.

    Returns:
        The resolved changeset and its summary.

    Raises:
        ChangeRefused: When an item is invalid on its own (a missing source,
            an occupied destination, a reserved path, a stale basis, a
            directory carrying hidden entries) or claims a path another item
            claims.
    """
    resolved, plan, _changed = await _resolve_all(changeset.operations, host=host)
    operations = tuple(
        op for item in resolved if not isinstance(op := item.operation, Upload)
    )

    return PlannedChangeset(
        Changeset(operations), await asyncio.to_thread(_summarize, resolved, plan)
    )
