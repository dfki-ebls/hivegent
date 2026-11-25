"""The units a changeset claims and the effects it resolves to."""

from dataclasses import dataclass, replace
from pathlib import PurePath, PurePosixPath
from typing import Self

from ...metadata import EntryOrigin
from ...store import Workspace
from ...tools.base import Direct
from ...tools.entries import (
    CASE_BASE_KINDS,
    ContentStat,
    EntryPaths,
    Part,
    resolve_tree_paths,
)
from ..operations import Item, Location, Root, Upload, changed_root, policy_of


def _depth(path: str | PurePath) -> int:
    return len(PurePosixPath(path).parts)


@dataclass(slots=True, frozen=True)
class _Unit:
    """What one item moves, deletes, or lands: an entry, or a whole tree.

    A tree is a directory, or a file of a root without a store, which has no entry.
    """

    root: Root
    paths: EntryPaths
    """The subject and its companions, an entry's stem or the tree's root."""
    tree: bool = False

    @classmethod
    def of_tree(cls, root: Root, path: str) -> Self:
        """The tree at *path*, with the cbrkit companions a workspace keeps beside it."""
        paths = (
            EntryPaths.tree(path)
            if root.store is None
            else resolve_tree_paths(root.path, path)
        )

        return cls(root, paths, tree=True)

    @property
    def path(self) -> str:
        """The entry's stem, or the tree's root."""
        return self.paths.stem_path

    @property
    def entry(self) -> EntryPaths | None:
        """The entry's paths, ``None`` for a tree."""
        return None if self.tree else self.paths

    @property
    def files(self) -> tuple[str, ...]:
        """The paths the unit consists of, everything below them included."""
        return self.paths.files

    def at(self, root: Root, path: str) -> Self:
        """The unit relocated to *path* in *root*, its companions renamed along."""
        return replace(self, root=root, paths=self.paths.at(path))


@dataclass(slots=True, frozen=True)
class _TextEffect:
    """A text document's new content, derived from the content it replaces."""

    target: Location
    current: str | None
    content: str
    data: bytes
    """*content* as the UTF-8 stored on disk, encoded once for every check and the write."""

    origin: Location | None
    """Where *current* lies before the changeset, ``None`` for nothing."""

    @property
    def store(self) -> Workspace | None:
        """The workspace whose rows the written file carries, ``None`` for a plain file."""
        return self.target.root.store

    @property
    def is_original(self) -> bool:
        """Whether the write lands on a workspace original whose projection is re-derived."""
        return self.store is not None and Part.of(self.target.path).kind == "original"

    @property
    def indexed(self) -> bool:
        """Whether the written file is a workspace description, chunked once it landed."""
        return (
            self.store is not None
            and Part.of(self.target.path).kind not in CASE_BASE_KINDS
        )


@dataclass(slots=True, frozen=True)
class _UploadEffect:
    """An upload's bytes landing as an entry at *target*."""

    upload: Upload
    target: Location
    original: tuple[str, bytes] | None
    """The original it writes, the target itself or a markdown's companion."""
    origin: EntryOrigin
    """How the entry came to be, the one it overwrites kept."""
    present: tuple[ContentStat | None, ...]
    """What lies at its files before the changeset, so a change made meanwhile shows."""
    replacing: bool
    """Whether it replaces an entry in place, whose stale asset rows then go."""
    superseded: _Unit | None
    """The entry it overwrites by deleting it first, since that has files the upload has not."""


@dataclass(slots=True, frozen=True)
class _Vacate:
    """A unit that leaves its place, landing at *destination* or deleted."""

    unit: _Unit
    destination: _Unit | None


@dataclass(slots=True, frozen=True)
class _MakeDir:
    target: Location


type _Effect = _TextEffect | _UploadEffect | _Vacate | _MakeDir


def _vacate_of(effect: _Effect) -> _Vacate | None:
    """What an item vacates: a move's or a delete's unit, or the entry an upload supersedes."""
    match effect:
        case _Vacate():
            return effect
        case _UploadEffect(superseded=_Unit() as unit):
            return _Vacate(unit, None)
        case _:
            return None


@dataclass(slots=True, frozen=True)
class _Resolved:
    """One validated item: its resolved form, what it does, and its report."""

    operation: Item
    effect: _Effect
    report: str

    @property
    def indexed(self) -> bool:
        """Whether the item changes a workspace, which is indexed and announced."""
        return changed_root(self.operation).store is not None

    @property
    def shown(self) -> bool:
        """Whether a person approving the changeset reads the item, which no direct folder's is."""
        return not isinstance(policy_of(self.operation), Direct)
