"""Where a changeset's paths lie: a casebase and the path local to its workspace.

The operations themselves are the plain, location-generic data of
:mod:`hivegent.changes`, and :func:`route` turns one spelled in canonical paths
into one the gateway in :mod:`~hivegent.workspace.changeset` plans and applies.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from ..changes import Changeset, CreateDir, Delete, Edit, Move, Operation, Write
from ..config import settings
from ..store import Casebase, route_path

__all__ = ["Location", "route"]


@dataclass(slots=True, frozen=True)
class Location:
    """A path local to one casebase's workspace.

    Attributes:
        store: The casebase whose workspace holds the path.
        path: The path relative to that workspace, where empty names its root.
    """

    store: Casebase
    path: str

    @classmethod
    def parse(cls, stores: Sequence[Casebase], canonical: str) -> Self:
        """Route a canonical ``~/...`` or ``@<group>/...`` path to its store.

        Raises:
            ValueError: When *canonical* names no store in *stores*.
        """
        return cls(*route_path(stores, canonical))

    @property
    def canonical(self) -> str:
        """The path as tools and routes spell it, prefix included."""
        return self.store.scope.render(self.path)

    @property
    def full_path(self) -> Path:
        """The path on disk, without creating the workspace it lies in."""
        return self.store.workspace_path(settings.data_dir) / self.path


def _routed(operation: Operation[str], stores: Sequence[Casebase]) -> Operation[Location]:
    def at(canonical: str) -> Location:
        return Location.parse(stores, canonical)

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


def route(changeset: Changeset[str], stores: Sequence[Casebase]) -> Changeset[Location]:
    """Route every canonical path of *changeset* to the one of *stores* it names.

    Raises:
        ValueError: When a path names no store in *stores*.
    """
    return Changeset(tuple(_routed(op, stores) for op in changeset.operations))
