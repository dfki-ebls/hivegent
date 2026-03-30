"""Casebase identity for user and group storage namespaces."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import sanitize_group_id, sanitize_user_id
from .tools.base import SearchPath, SearchPathFilterFunc

__all__ = [
    "Casebase",
    "build_search_paths",
]


@dataclass(slots=True, frozen=True)
class Casebase:
    """Identifies a casebase namespace (user or group).

    Each casebase maps to a filesystem directory under
    ``data/{users,groups}/<id>/`` containing a workspace, metadata,
    a LanceDB index, and conversation state.
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
        """Stable opaque key for caching and identification."""
        return f"{self.kind}:{self.id}"

    @property
    def prefix(self) -> str | None:
        """Display prefix for document filenames from this store."""
        return f"@{self.id}" if self.kind == "group" else None

    def root_path(self, data_dir: Path) -> Path:
        """Return the root path for this store without creating directories.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's root directory.
        """
        subdir = "users" if self.kind == "user" else "groups"
        return data_dir / subdir / self.id

    def root_dir(self, data_dir: Path) -> Path:
        """Return the root directory for this store, creating it if needed.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's root directory.
        """
        path = self.root_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace_path(self, data_dir: Path) -> Path:
        """Return the workspace path without creating directories.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's workspace directory.
        """
        return self.root_path(data_dir) / "workspace"

    def workspace_dir(self, data_dir: Path) -> Path:
        """Return the workspace directory for this store, creating it if needed.

        Contains source files, markdown companions, and recursive asset directories.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's workspace directory.
        """
        path = self.workspace_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def metadata_path(self, data_dir: Path) -> Path:
        """Return the metadata path without creating directories.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's metadata directory.
        """
        return self.root_path(data_dir) / "metadata"

    def metadata_dir(self, data_dir: Path) -> Path:
        """Return the metadata directory for this store, creating it if needed.

        Contains per-entry JSON files with chunk data and stem-entry metadata.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's metadata directory.
        """
        path = self.metadata_path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def lancedb_dir(self, data_dir: Path) -> Path:
        """Return the LanceDB directory for this store.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's LanceDB directory.
        """
        path = self.root_dir(data_dir) / "lancedb"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def conversations_dir(self, data_dir: Path) -> Path:
        """Return the conversations directory for this store.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's conversations directory.
        """
        path = self.root_dir(data_dir) / "conversations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def conversation_path(self, data_dir: Path, conversation_id: str) -> Path:
        """Return the path to a conversation JSON file for this store."""
        return self.conversations_dir(data_dir) / f"{conversation_id}.json"

    def tokens_path(self, data_dir: Path) -> Path:
        """Return the tokens JSON path for this store."""
        return self.root_dir(data_dir) / "tokens.json"

    def memory_path(self, data_dir: Path) -> Path:
        """Return the memory markdown path for this store."""
        return self.root_dir(data_dir) / "memory.md"


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
