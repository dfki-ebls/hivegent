"""Casebase identity for user and group storage namespaces."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import sanitize_group_id, sanitize_user_id

__all__ = [
    "Casebase",
]


@dataclass(slots=True, frozen=True)
class Casebase:
    """Identifies a casebase namespace (user or group).

    Each casebase maps to a filesystem directory under
    ``data/{users,groups}/<id>/`` containing documents, chunks,
    a LanceDB index, and optionally original binary files.
    """

    kind: Literal["user", "group"]
    id: str

    def __post_init__(self) -> None:
        if self.kind == "user":
            sanitize_user_id(self.id)
        else:
            sanitize_group_id(self.id)

    @property
    def store_key(self) -> str:
        """Stable opaque key for caching and identification."""
        return f"{self.kind}:{self.id}"

    def root_dir(self, data_dir: Path) -> Path:
        """Return the root directory for this store.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's root directory.
        """
        subdir = "users" if self.kind == "user" else "groups"
        path = data_dir / subdir / self.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def documents_dir(self, data_dir: Path) -> Path:
        """Return the documents directory for this store.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's documents directory.
        """
        path = self.root_dir(data_dir) / "documents"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def chunks_dir(self, data_dir: Path) -> Path:
        """Return the chunks directory for this store.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's chunks directory.
        """
        path = self.root_dir(data_dir) / "chunks"
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

    def originals_dir(self, data_dir: Path) -> Path:
        """Return the originals directory for this store.

        Args:
            data_dir: The application data root directory.

        Returns:
            Path to the store's originals directory.
        """
        path = self.root_dir(data_dir) / "originals"
        path.mkdir(parents=True, exist_ok=True)
        return path
