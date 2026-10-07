"""Filesystem primitives shared by the workspace, ``/tmp``, and staged changesets."""

import shutil
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

__all__ = ["atomic_write", "remove_path"]


def remove_path(path: Path) -> None:
    """Remove a file, a symlink, or a whole directory tree, tolerating its absence.

    Absence is the only tolerated failure, so a tree that could not be removed
    still raises rather than leaving stale files behind unreported.
    """
    if path.is_dir() and not path.is_symlink():
        with suppress(FileNotFoundError):
            shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def atomic_write(path: Path, data: bytes) -> None:
    """Write *path* whole through a sibling and a rename, so no reader sees half of it.

    Creates the parents, and replaces a directory standing at *path*.
    """
    if path.is_dir():
        remove_path(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{uuid4().hex}")

    try:
        _ = partial.write_bytes(data)
        _ = partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
