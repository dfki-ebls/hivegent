"""The planner state: what the items claim, where they land, and what they read."""

import os
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path, PurePosixPath

from ...config import sanitize_document_path
from ...tools.changes import Basis, ChangeRefused, Delete, DeleteKind, Move
from ...tools.entries import (
    EntryPaths,
    Listdir,
    find_original_for_stem,
    folds_case,
    is_below,
    is_description_file,
    is_inside_assets_dir,
    list_names,
    path_key,
    rebase,
    resolve_entry_paths,
    respell,
    stem_path_from_reference,
)
from ..documents import _check_basis
from ..operations import Item, Location, Root, Upload
from ..paths import DIRECTORY_PATH_REQUIRED, _parent_is_file, _shown, document_not_found
from .model import _depth, _Unit
from .rules import (
    _DOCUMENT_EXISTS,
    _NOT_FOUND,
    _carries_hidden,
    _claimed_twice,
    _crosses_roots,
    _describes_nothing,
    _destination_exists_at,
    _inaccessible,
    _into_itself,
    _is_existing_directory,
    _kept,
    _same_paths,
)

type _Key = tuple[str, str]


type _Claims = dict[_Key, tuple[int, _Unit]]
"""Units by every path they consist of, with the item that claims each."""


@dataclass(slots=True, frozen=True)
class _Plan:
    """The units a changeset vacates and lands, and the paths it fills.

    One plan is one pass over roots that do not change meanwhile, so it
    caches what it reads of the disk: each root's case folding and each
    directory's listing.
    """

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
    overwrites: list[_Unit] = field(default_factory=list)
    """The entries uploads replace, each claiming a stem but for its own files."""
    evicted: list[_Unit] = field(default_factory=list)
    """The files a root's quota evicts to fit the changeset."""
    described: list[tuple[Location, str | None]] = field(default_factory=list)
    """The asset descriptions the changeset keeps, each with the asset it needs."""
    folded: dict[str, bool] = field(default_factory=dict)
    listdir: Listdir = field(default_factory=lambda: cache(list_names))

    def key(self, root: Root, path: str) -> _Key:
        """What two spellings of one path share, compared as the root's filesystem does."""
        folded = self.folded.get(root.store_key)

        if folded is None:
            folded = self.folded[root.store_key] = folds_case(root.path)

        return root.store_key, path_key(path, folded=folded)

    def check_reserved(self, root: Root, path: str) -> None:
        """Refuse a path *root* keeps from ordinary changes, which the host may make."""
        if not self.host and root.policy.reserves(root.path, path):
            raise ChangeRefused(
                status_code=400, detail=_kept(_shown(root, path)).current
            )

    def sanitized(self, location: Location, *, keep_name: bool = False) -> Location:
        """*location* made safe and spelled the way the disk spells what exists of it.

        *keep_name* leaves the last segment as given, which is how a destination
        renames something case-only on a case-insensitive filesystem.
        """
        try:
            path = PurePosixPath(sanitize_document_path(location.path))
        except ValueError as exc:
            raise ChangeRefused(status_code=400, detail=str(exc)) from exc

        root = location.root
        directory = root.path
        # Every path a change names passes here.
        self.check_reserved(root, str(path))

        if keep_name:
            spelled = str(
                PurePosixPath(
                    respell(directory, str(path.parent), self.listdir), path.name
                )
            )
        else:
            spelled = respell(directory, str(path), self.listdir)

        # Asked again here, so a changeset staged under another filter reaches nothing this one hides.
        if (visible := root.search_path.filter_func) is not None and not visible(
            spelled
        ):
            raise ChangeRefused(
                status_code=403, detail=_inaccessible(_shown(root, spelled)).current
            )

        return Location(root, spelled)

    def holder(
        self, claims: _Claims, root: Root, path: str, skip: int | None = None
    ) -> tuple[int, _Unit] | None:
        """The deepest unit in *claims* holding *path*, ignoring the item *skip*."""
        pure = PurePosixPath(path)

        for candidate in (pure, *pure.parents):
            hit = (
                claims.get(self.key(root, str(candidate))) if candidate.parts else None
            )

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
                raise ChangeRefused(
                    status_code=400,
                    detail=_claimed_twice(_shown(unit.root, path)).current,
                )

            claims[key] = (index, unit)

    def description(self, location: Location) -> _Unit | None:
        """The unit of an asset's description, the description alone, ``None`` for any other path.

        The asset is the original it describes, which :meth:`check_described`
        then requires once the changeset applied.
        """
        root, path = location.root, location.path

        if (
            root.store is None
            or not is_inside_assets_dir(path)
            or not is_description_file(path)
        ):
            return None

        stem = stem_path_from_reference(path)
        self.described.append(
            (location, find_original_for_stem(root.path, stem, self.listdir))
        )

        return _Unit(root, EntryPaths.entry(stem))

    def check_described(self) -> None:
        """Refuse an asset's description whose asset is gone once the changeset applied."""
        for location, asset in self.described:
            if asset is None or self.occupied(location.root, asset) is None:
                raise ChangeRefused(
                    status_code=404,
                    detail=_describes_nothing(location.canonical).current,
                )

    def fill(self, index: int, target: Location) -> None:
        key = self.key(target.root, target.path)

        if self.filled.setdefault(key, index) != index:
            raise ChangeRefused(
                status_code=400, detail=_claimed_twice(target.canonical).current
            )

    def claim_write(self, index: int, unit: _Unit) -> None:
        """Claim the files a text item writes, an original's projection included.

        They share one folder and one stem, so both are checked once.
        """
        root, first = unit.root, unit.files[0]
        self.check_parents(root, first)

        for path in unit.files:
            target = Location(root, path)
            self.fill(index, target)
            self.written[self.key(root, path)] = target

    def origin(self, root: Root, path: str, skip: int | None = None) -> Location | None:
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

            return (
                Location(source.root, initial) if owner and owner[0] == index else None
            )

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
                raise ChangeRefused(
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
                    raise ChangeRefused(
                        status_code=409,
                        detail=_parent_is_file(target.canonical).current,
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

    def check_slot(self, unit: _Unit, *, overwrite: bool = False) -> None:
        """Refuse a new original whose stem holds another entry once the changeset applied.

        An upload's slot check, asked of the final state like every other check
        here: a stem the changeset vacates is free, and one it moves an entry
        onto, or writes another part of, is not.  An *overwrite* may replace
        the files the unit consists of and its ``.assets`` directory, and
        nothing else at the stem.
        """
        root, stem = unit.root, PurePosixPath(unit.path)

        for path in unit.files:
            occupant = self.occupied(root, path)

            if occupant is not None and (occupant.is_dir() or not overwrite):
                detail = (
                    _is_existing_directory(_shown(root, path))
                    if occupant.is_dir()
                    else _DOCUMENT_EXISTS
                )
                raise ChangeRefused(status_code=409, detail=detail.current)

        own = {self.key(root, path) for path in unit.files}

        if overwrite:
            own.add(self.key(root, unit.paths.path("assets")))
        name = self.key(root, stem.name)

        for sibling_name in self._final_names(root, str(stem.parent)):
            sibling = str(stem.parent / sibling_name)
            key = self.key(root, sibling)

            if (
                key not in own
                and self.key(root, PurePosixPath(sibling_name).stem) == name
                and (key in self.written or self.occupied(root, sibling))
            ):
                raise ChangeRefused(status_code=409, detail=_DOCUMENT_EXISTS.current)


type _Sources = dict[int, tuple[Location, bool]]
"""Each move's and delete's source, spelled as the disk does, and whether it is a directory."""


def _source(
    plan: _Plan,
    location: Location,
    basis: Basis | None,
    expect: DeleteKind | None,
) -> tuple[Location, bool]:
    location = plan.sanitized(location)
    root, path = location.root, location.path
    full = location.full_path
    _check_basis(_shown(root, path), full, basis)
    is_dir = full.is_dir()

    if expect is not None and expect != ("dir" if is_dir else "entry"):
        raise ChangeRefused(
            status_code=404, detail=_NOT_FOUND[expect](_shown(root, path)).current
        )

    # A plain file has no row to stand in for it, so it must be on disk.
    if root.store is None and not os.path.lexists(full):
        raise ChangeRefused(
            status_code=404, detail=document_not_found(_shown(root, path)).current
        )

    # A directory move or delete carries everything below it, so the filter's
    # own entries are asked, not the tree, and the refusal names only the
    # directory, keeping hidden paths private.
    hidden = root.search_path.filter_func

    if is_dir and hidden is not None and hidden.hides_within(path):
        raise ChangeRefused(
            status_code=403, detail=_carries_hidden(_shown(root, path)).current
        )

    return location, is_dir


def _locate_sources(plan: _Plan, operations: list[Item]) -> _Sources:
    """Spell every move's and delete's source, and every upload's target, as the disk does, in place."""
    sources: _Sources = {}

    for index, op in enumerate(operations):
        match op:
            case Move(source=source, basis=basis):
                sources[index] = _source(plan, source, basis, None)
                operations[index] = replace(op, source=sources[index][0])
            case Delete(target=target, basis=basis, expect=expect):
                if not target.path:
                    # A bare scope root would wipe the workspace, and the full wipe of
                    # files and rows together is `delete_all`.
                    raise ChangeRefused(
                        status_code=400, detail=DIRECTORY_PATH_REQUIRED.current
                    )

                sources[index] = _source(plan, target, basis, expect)
                operations[index] = replace(op, target=sources[index][0])
            case Upload(target=target):
                operations[index] = replace(op, target=plan.sanitized(target))
            case _:
                pass

    return sources


def _landing(
    operation: Move[Location], index: int, source: _Unit, plan: _Plan
) -> Move[Location]:
    """Resolve where a move lands, and claim it."""
    destination = operation.destination

    if destination.path:
        destination = plan.sanitized(destination, keep_name=True)

    root, path = destination.root, destination.path
    same_root = root == source.root

    # Only rows carry a file between roots, so nothing moves to or from a plain one.
    if not same_root and None in (root.store, source.root.store):
        raise ChangeRefused(
            status_code=400,
            detail=_crosses_roots(
                operation.source.canonical, destination.canonical
            ).current,
        )

    named = source.entry.description_path if source.entry else source.path

    if not path or (
        destination.full_path.is_dir() and plan.holder(plan.vacated, root, path) is None
    ):
        directory = respell(root.path, path, plan.listdir)
        path = str(PurePosixPath(directory, PurePosixPath(named).name))

    if source.entry is None:
        if same_root and is_below(
            plan.key(root, path)[1], plan.key(root, source.path)[1]
        ):
            raise ChangeRefused(
                status_code=400,
                detail=_into_itself(_shown(source.root, source.path)).current,
            )

        plan.check_reserved(root, path)
    else:
        path = stem_path_from_reference(path)
        plan.check_reserved(root, path)

    if same_root and path == source.path:
        raise ChangeRefused(
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
            resolve_entry_paths(origin.root.path, origin.path, plan.listdir)
            .at(destination.path)
            .files
        )

    for path in sorted(paths):
        if plan.occupied(destination.root, path, skip=index) is not None:
            raise ChangeRefused(
                status_code=409,
                detail=_destination_exists_at(_shown(destination.root, path)).current,
            )

        plan.check_parents(destination.root, path)
