"""Filesystem primitives shared by the workspace, ``/tmp``, and staged changesets."""

import os
import shutil
from contextlib import suppress
from pathlib import Path
from stat import S_ISDIR
from uuid import uuid4

__all__ = ["atomic_write", "disk_size", "remove_path"]


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


def disk_size(path: Path) -> int:
    """The bytes the files at or below *path* hold, none for nothing."""
    try:
        own = os.lstat(path)
    except FileNotFoundError:
        return 0

    if not S_ISDIR(own.st_mode):
        return own.st_size

    return sum(
        os.lstat(os.path.join(directory, name)).st_size
        for directory, _dirs, files in os.walk(path)
        for name in files
    )


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
