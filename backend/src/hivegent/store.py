"""Casebase identity for user and group storage namespaces.

A :class:`Casebase` is an immutable identifier — ``(kind, id)`` — for
one user or group's slice of the system.  Only the workspace files
live on disk per casebase, under ``<data_dir>/workspace/<store_key>/``
where ``store_key`` is the same ``"user:<id>"`` / ``"group:<id>"`` token
that scopes the ``owner_*`` columns on ``documents``; chunk visibility
derives from the document FK — one identifier keys filesystem and SQL
end-to-end.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, cast, get_args

from .config import sanitize_group_id, sanitize_user_id
from .tools.base import SearchPath, SearchPathFilterFunc

__all__ = [
    "Casebase",
    "CasebaseKind",
    "build_search_paths",
]

CasebaseKind = Literal["user", "group"]


@dataclass(slots=True, frozen=True)
class Casebase:
    """Identifies a casebase namespace (user or group).

    Each casebase owns a workspace directory at
    ``<data_dir>/workspace/<store_key>/`` and rows in the global SQL
    database scoped by ``owner_user_id`` / ``owner_group_id``.
    """

    kind: CasebaseKind
    id: str

    @classmethod
    def for_user(cls, user_id: str) -> Self:
        """Build a user-scoped casebase."""
        return cls(kind="user", id=user_id)

    @classmethod
    def for_group(cls, group_id: str) -> Self:
        """Build a group-scoped casebase."""
        return cls(kind="group", id=group_id)

    @classmethod
    def from_store_key(cls, store_key: str) -> Self:
        """Parse a ``store_key`` back into a :class:`Casebase`.

        Inverse of :attr:`store_key`.  Raises :class:`ValueError` when
        the input is not a recognised ``user:<id>`` / ``group:<id>``
        token.
        """
        kind, sep, identifier = store_key.partition(":")
        if not sep or kind not in get_args(CasebaseKind):
            raise ValueError(f"Invalid store_key: {store_key!r}")
        return cls(kind=cast(CasebaseKind, kind), id=identifier)

    def __post_init__(self) -> None:
        if self.kind == "user":
            sanitize_user_id(self.id)
        else:
            sanitize_group_id(self.id)

    @property
    def store_key(self) -> str:
        """Stable opaque key for the filesystem, SQL, and vector index scoping."""
        return f"{self.kind}:{self.id}"

    @property
    def prefix(self) -> str | None:
        """Display prefix for document filenames from this store."""
        return f"@{self.id}" if self.kind == "group" else None

    @staticmethod
    def workspace_root(data_dir: Path) -> Path:
        """Return the root directory holding every casebase workspace."""
        return data_dir / "workspace"

    def workspace_path(self, data_dir: Path) -> Path:
        """Return the workspace path without creating directories."""
        return self.workspace_root(data_dir) / self.store_key

    def workspace_dir(self, data_dir: Path) -> Path:
        """Return the workspace directory, creating it if needed.

        Contains source files, markdown companions, and recursive
        asset directories.  No other on-disk artifacts live here.
        """
        path = self.workspace_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


def build_search_paths(
    store: Casebase,
    group_stores: Sequence[Casebase],
    data_dir: Path,
    *,
    dir_fn: Callable[[Casebase, Path], Path] = Casebase.workspace_dir,
    filter_for_store: Callable[[Casebase], SearchPathFilterFunc] | None = None,
) -> tuple[SearchPath, ...]:
    """Build :class:`SearchPath` entries for a user store and its groups.

    Args:
        store: The user's personal casebase.
        group_stores: Group casebases the user can access.
        data_dir: Application data root directory.
        dir_fn: Method to obtain the directory path from each store
            (default :meth:`Casebase.workspace_dir`).
        filter_for_store: Optional callable returning a file filter for
            each store.
    """
    get_filter = filter_for_store or (lambda _: None)
    return tuple(
        SearchPath(
            path=dir_fn(s, data_dir),
            prefix=s.prefix,
            filter_func=get_filter(s),
        )
        for s in (store, *group_stores)
    )
