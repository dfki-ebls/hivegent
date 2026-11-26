"""Where a changeset's paths lie: a root and the path local to it.

The operations themselves are the plain, location-generic data of
:mod:`hivegent.tools.changes`, and :func:`route` turns one spelled in canonical paths
into one the gateway in :mod:`~hivegent.workspace.changeset` plans and applies.
An :class:`Upload` is the one item that exists only here, routed already,
since it carries bytes no stored changeset may hold.

A :class:`Root` answers for the rules its changes follow, so the gateway never
asks which kind it holds: one of a :class:`~hivegent.store.Workspace`, whose
entries carry rows, an index, and announcements, or one of plain files, such as
a conversation's ``/tmp`` or a skill library.
"""

import os
from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Self

from exar.memory.mounts import Mount, claim

from ..files import disk_size
from ..metadata import EntryOrigin
from ..shared.l10n import Localized, format_bytes
from ..store import Workspace
from ..tools.base import CommitPolicy, PathFilter, SearchPath, strictest
from ..tools.changes import (
    Basis,
    ChangeRefused,
    Changeset,
    CreateDir,
    Delete,
    Edit,
    Move,
    Operation,
    Write,
)
from ..tools.entries import is_description_file, stem_path_from_reference
from ..types import ProgressReporter

__all__ = [
    "Item",
    "Location",
    "Quota",
    "Root",
    "Upload",
    "changed_root",
    "locations",
    "policy_of",
    "route",
]


def _no_workspace(path: str) -> Localized[str]:
    return Localized(
        en=f"No accessible workspace for {path!r}",
        de=f"Kein zugänglicher Arbeitsbereich für „{path}“",
    )


def _over_quota(root: str, size: int, limit: int) -> Localized[str]:
    return Localized(
        en=(
            f"This would grow {root} to {format_bytes(size)}, over the "
            f"{format_bytes(limit)} it may hold. Remove what it no longer needs."
        ),
        de=(
            f"Das würde {root} auf {format_bytes(size)} anwachsen lassen, mehr als die "
            f"erlaubten {format_bytes(limit)}. Entferne, was nicht mehr gebraucht wird."
        ),
    )


@dataclass(slots=True, frozen=True)
class Quota:
    """What a folder may hold once a change is written.

    Attributes:
        max_bytes: The cap on every file in the folder together.
        evictable: A folder directly below it whose files the host may evict,
            oldest first, to fit a change of its own.
    """

    max_bytes: int
    evictable: str | None = None

    def admit(
        self,
        folder: Path,
        shown: str,
        growth: int,
        keep: AbstractSet[Path],
        *,
        evict: bool,
    ) -> list[Path]:
        """The files to evict so that *growth* more bytes fit in *folder*, none of *keep*.

        A folder already over its cap may still shrink, so a run can replace
        its own state while it sits at the limit, and the folder is only
        walked when the change grows it.

        Raises:
            ChangeRefused: When the change does not fit even without them,
                naming the folder as *shown*.
        """
        excess = disk_size(folder) + growth - self.max_bytes if growth > 0 else 0
        evicted: list[Path] = []

        if excess > 0 and evict and self.evictable is not None:
            for _mtime, size, path in _oldest(folder / self.evictable, keep):
                if excess <= 0:
                    break

                evicted.append(path)
                excess -= size

        if excess > 0:
            detail = _over_quota(shown, self.max_bytes + excess, self.max_bytes)
            raise ChangeRefused(status_code=413, detail=detail.current)

        return evicted


def _oldest(folder: Path, keep: AbstractSet[Path]) -> list[tuple[float, int, Path]]:
    """The files directly in *folder* besides *keep*, oldest first, from one listing."""
    found: list[tuple[float, int, Path]] = []

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)

                if entry.is_file(follow_symlinks=False) and path not in keep:
                    stat = entry.stat(follow_symlinks=False)
                    found.append((stat.st_mtime, stat.st_size, path))

    except FileNotFoundError:
        return []

    return sorted(found)


@dataclass(slots=True, frozen=True)
class Root:
    """What a changeset's location lies in: a search path with the rules its commit follows.

    Attributes:
        search_path: The folder as the tools resolve it, with the filter of
            what the caller may see and the policy a change commits by.
        quota: What it may hold once a change is written, unbounded when ``None``.
        store: The workspace whose rows, index, in-flight claims, and
            announcements its changes carry, ``None`` for a root of plain
            files written directly.
    """

    search_path: SearchPath
    quota: Quota | None = None
    store: Workspace | None = None

    @classmethod
    def of(cls, store: Workspace, filter_func: PathFilter | None = None) -> Self:
        """The root of a workspace, its paths filtered by *filter_func*."""
        return cls(store.search_path(filter_func), store=store)

    @property
    def path(self) -> Path:
        """The directory it holds, resolved, without creating it."""
        return self.search_path.path

    @property
    def mount(self) -> Mount:
        """The directory it holds, whose target is the prefix its canonical paths lead with."""
        return self.search_path.mount

    @property
    def policy(self) -> CommitPolicy:
        """Whether a change to it is approved, and what no ordinary change may touch."""
        return self.search_path.policy

    @property
    def store_key(self) -> str:
        """What identifies it, and orders its lock beside every other root's."""
        return f"folder:{self.path}" if self.store is None else self.store.store_key


@dataclass(slots=True, frozen=True)
class Location:
    """A path local to one root.

    Attributes:
        root: The root holding the path.
        path: The path relative to that root, where empty names the root itself.
    """

    root: Root
    path: str

    @property
    def canonical(self) -> str:
        """The path as tools and routes spell it, prefix included."""
        return self.root.mount.render(self.path)

    @property
    def full_path(self) -> Path:
        """The path on disk, without creating the root it lies in."""
        return self.root.path / self.path


@dataclass(slots=True, frozen=True)
class Upload:
    """Land a file's bytes as an entry, converted the way every upload is.

    Only the host builds one, from bytes it received, so it carries what no
    stored changeset may: the bytes, the pipelines, and the progress the
    conversion reports to.

    Attributes:
        target: The file, a markdown description or an original whose
            projection the conversion derives.
        content: Its bytes.
        modified_at: When the content last changed, which dates every file
            the entry lands, now when unknown.
        origin: How a new entry came to be.
        companion: A markdown upload's original, a sibling path local to the
            target's root with its bytes.
        overwrite: Whether it replaces the entry at its stem, keeping that
            entry's origin, which otherwise must not exist.
        basis: What the caller saw of the target, if anything.
        progress: Where the conversion reports its stages.
    """

    target: Location
    content: bytes
    modified_at: datetime | None = None
    origin: EntryOrigin = "upload"
    companion: tuple[str, bytes] | None = None
    overwrite: bool = False
    basis: Basis | None = None
    progress: ProgressReporter | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.modified_at is not None and self.modified_at.tzinfo is None:
            raise ValueError("An upload's modification time must name its zone")

        if self.companion is None:
            return

        path, original = self.target.path, self.companion[0]

        if not is_description_file(path) or is_description_file(original):
            raise ValueError("A companion original belongs to a markdown upload")

        if stem_path_from_reference(original) != stem_path_from_reference(path):
            raise ValueError("A companion original must share the upload's stem")


type Item = Operation[Location] | Upload
"""What the gateway commits: an operation routed to its root, or an upload."""


def changed_root(operation: Item) -> Root:
    """The root an item changes, a move's source's, since no move leaves a folder."""
    return (
        operation.source.root if isinstance(operation, Move) else operation.target.root
    )


def locations(items: Iterable[Item]) -> tuple[Location, ...]:
    """Every path *items* name, in order, as :attr:`Changeset.locations` reads them."""
    return tuple(
        location
        for op in items
        for location in (
            (op.source, op.destination) if isinstance(op, Move) else (op.target,)
        )
    )


def policy_of(operation: Item) -> CommitPolicy:
    """The policy an item answers to, the strictest of the roots it names, since a move changes both ends."""
    return strictest(location.root.policy for location in locations((operation,)))


def _located(roots: Sequence[Root], canonical: str) -> Location:
    mounts = [root.mount for root in roots]

    if (claimed := claim(mounts, canonical)) is None:
        raise ValueError(_no_workspace(canonical).current)

    return Location(roots[mounts.index(claimed[0])], claimed[1])


def _routed(operation: Operation[str], roots: Sequence[Root]) -> Operation[Location]:
    def at(canonical: str) -> Location:
        return _located(roots, canonical)

    match operation:
        case Write(target, content, mode, basis):
            return Write(at(target), content, mode, basis)
        case Edit(target, edits, basis):
            return Edit(at(target), edits, basis)
        case Move(source, destination, basis):
            return Move(at(source), at(destination), basis)
        case Delete(target, basis, expect):
            return Delete(at(target), basis, expect)
        case CreateDir(target):
            return CreateDir(at(target))


def route(changeset: Changeset[str], roots: Sequence[Root]) -> Changeset[Location]:
    """Route every canonical path of *changeset* to the one of *roots* it names.

    Raises:
        ValueError: When a path names no root in *roots*.
    """
    return Changeset(tuple(_routed(op, roots) for op in changeset.operations))
