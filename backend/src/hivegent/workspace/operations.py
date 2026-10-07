"""Where a changeset's paths lie: a root and the path local to it.

The operations themselves are the plain, location-generic data of
:mod:`hivegent.changes`, and :func:`route` turns one spelled in canonical paths
into one the gateway in :mod:`~hivegent.workspace.changeset` plans and applies.

A :class:`Root` answers for the rules its changes follow, so the gateway never
asks which kind it holds: a :class:`~hivegent.store.Casebase`, whose workspace
holds entries with rows, an index, and announcements, or a :class:`Folder`,
whose plain files are written directly, such as a conversation's ``/tmp``.
"""

import os
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import HTTPException

from ..changes import Changeset, CreateDir, Delete, Edit, Move, Operation, Write
from ..files import disk_size
from ..humanize import format_bytes
from ..l10n import Localized
from ..store import Casebase
from ..tools.base import CommitPolicy, Direct
from ..tools.scope import Scope

__all__ = ["Folder", "Location", "Quota", "Root", "changed_root", "route"]


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
        self, folder: Path, shown: str, growth: int, keep: AbstractSet[Path], *, evict: bool
    ) -> list[Path]:
        """The files to evict so that *growth* more bytes fit in *folder*, none of *keep*.

        A folder already over its cap may still shrink, so a run can replace
        its own state while it sits at the limit, and the folder is only
        walked when the change grows it.

        Raises:
            HTTPException: When the change does not fit even without them,
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
            raise HTTPException(status_code=413, detail=detail.current)

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


class Root(Protocol):
    """What a changeset's location lies in, answering for the rules its commit follows."""

    @property
    def store_key(self) -> str:
        """What identifies it, and orders its lock beside every other root's."""
        ...

    @property
    def scope(self) -> Scope:
        """The prefix its canonical paths lead with."""
        ...

    @property
    def path(self) -> Path:
        """The directory it holds, without creating it."""
        ...

    @property
    def policy(self) -> CommitPolicy:
        """Whether a change to it is approved, and what no ordinary change may touch."""
        ...

    @property
    def quota(self) -> Quota | None:
        """What it may hold once a change is written, unbounded when ``None``."""
        ...

    @property
    def store(self) -> Casebase | None:
        """The casebase whose rows, index, in-flight claims, and announcements its changes carry.

        ``None`` for a root of plain files written directly.
        """
        ...


@dataclass(slots=True, frozen=True)
class Folder:
    """A root whose plain files are written directly.

    No rows, index, in-flight claims, or announcements, and no approval,
    while every other rule of a changeset holds as it does for a workspace.

    Attributes:
        path: The folder on disk.
        scope: The prefix its canonical paths lead with.
        policy: Its search path's policy, naming the folder only the host changes.
        quota: What it may hold once a change is written.
    """

    path: Path
    scope: Scope
    policy: Direct
    quota: Quota

    @property
    def store_key(self) -> str:
        """What orders and identifies its lock beside every casebase's."""
        return f"folder:{self.path}"

    @property
    def store(self) -> None:
        """No casebase, since its files carry no rows."""
        return None


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
        return self.root.scope.render(self.path)

    @property
    def full_path(self) -> Path:
        """The path on disk, without creating the root it lies in."""
        return self.root.path / self.path


def changed_root(operation: Operation[Location]) -> Root:
    """The root an item changes, a move's source's, since no move leaves a folder."""
    return operation.source.root if isinstance(operation, Move) else operation.target.root


def _located(roots: Sequence[Root], canonical: str) -> Location:
    for root in roots:
        if (local := root.scope.strip_prefix(canonical)) is not None:
            return Location(root, local)

    raise ValueError(_no_workspace(canonical).current)


def _routed(operation: Operation[str], roots: Sequence[Root]) -> Operation[Location]:
    def at(canonical: str) -> Location:
        return _located(roots, canonical)

    match operation:
        case Write(target, content, mode, basis, chunking):
            return Write(at(target), content, mode, basis, chunking)
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
