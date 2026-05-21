"""Casebase identity for user and group storage namespaces.

A :class:`Casebase` is an immutable identifier — ``(kind, id)`` — for
one user or group's slice of the system.  Only the workspace files
live on disk per casebase, under ``<data_dir>/workspace/<kind>/<id>/``.
Everything else (documents, chunks, memory, conversations, tokens) is
stored in the global SQL database; LanceDB rows are scoped by
:attr:`Casebase.store_key` instead of separate directories.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import sanitize_group_id, sanitize_user_id
from .tools.base import SearchPath, SearchPathFilterFunc

__all__ = [
    "Casebase",
    "build_search_paths",
    "lancedb_dir",
]


def lancedb_dir(data_dir: Path) -> Path:
    """Return the global LanceDB directory, creating it if needed."""
    path = data_dir / "lancedb"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(slots=True, frozen=True)
class Casebase:
    """Identifies a casebase namespace (user or group).

    Each casebase owns a workspace directory at
    ``<data_dir>/workspace/<kind>/<id>/`` plus rows in the global SQL
    database scoped by ``owner_user_id`` / ``owner_group_id``.
    """

    kind: Literal["user", "group"]
    id: str

    @classmethod
    def for_user(cls, user_id: str) -> "Casebase":
        """Build a user-scoped casebase."""
        return cls(kind="user", id=user_id)

    @classmethod
    def for_group(cls, group_id: str) -> "Casebase":
        """Build a group-scoped casebase."""
        return cls(kind="group", id=group_id)

    def __post_init__(self) -> None:
        if self.kind == "user":
            sanitize_user_id(self.id)
        else:
            sanitize_group_id(self.id)

    @property
    def store_key(self) -> str:
        """Stable opaque key for caching and SQL/LanceDB scoping."""
        return f"{self.kind}:{self.id}"

    @property
    def prefix(self) -> str | None:
        """Display prefix for document filenames from this store."""
        return f"@{self.id}" if self.kind == "group" else None

    def workspace_path(self, data_dir: Path) -> Path:
        """Return the workspace path without creating directories."""
        return data_dir / "workspace" / self.kind / self.id

    def workspace_dir(self, data_dir: Path) -> Path:
        """Return the workspace directory, creating it if needed.

        Contains source files, markdown companions, and recursive
        asset directories.  No other on-disk artifacts live here.
        """
        path = self.workspace_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


def build_search_paths(
    store: "Casebase",
    group_stores: Sequence["Casebase"],
    data_dir: Path,
    *,
    dir_fn: Callable[["Casebase", Path], Path] = Casebase.workspace_dir,
    filter_for_store: Callable[["Casebase"], SearchPathFilterFunc] | None = None,
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
    paths: list[SearchPath] = [
        SearchPath(
            path=dir_fn(store, data_dir),
            prefix=store.prefix,
            filter_func=get_filter(store),
        ),
    ]
    for gs in group_stores:
        paths.append(
            SearchPath(
                path=dir_fn(gs, data_dir),
                prefix=gs.prefix,
                filter_func=get_filter(gs),
            )
        )
    return tuple(paths)
