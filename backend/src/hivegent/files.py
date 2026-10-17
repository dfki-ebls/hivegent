"""Filesystem primitives shared by the workspace, ``/tmp``, and staged changesets."""

import os
import shutil
import time
from collections.abc import Iterable
from contextlib import suppress
from itertools import chain
from pathlib import Path
from stat import S_ISDIR
from uuid import uuid4

__all__ = [
    "atomic_write",
    "disk_size",
    "is_blocked_by_other",
    "remove_path",
    "remove_paths",
    "rmdir_if_empty",
    "touched_since",
]


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


def remove_paths(paths: Iterable[Path]) -> None:
    """:func:`remove_path` each of *paths*."""
    for path in paths:
        remove_path(path)


def rmdir_if_empty(directory: Path) -> None:
    """Remove *directory* if it is empty, and leave it otherwise."""
    with suppress(OSError):
        directory.rmdir()


def touched_since(path: Path, cutoff: float) -> bool:
    """Whether anything at or below *path* changed after *cutoff*, stopping at the first.

    A file that vanishes while the tree is walked was just in use, so it
    counts as touched.
    """
    return any(_mtime(entry) >= cutoff for entry in chain((path,), path.rglob("*")))


def is_blocked_by_other(target: Path, source: Path) -> bool:
    """Whether *target* exists as a node distinct from *source*.

    ``exists()`` alone cannot tell another file from *source* under another
    spelling, which a case- or normalization-insensitive filesystem (macOS)
    resolves to the same inode, so only a distinct inode blocks.
    """
    if not target.exists():
        return False

    try:
        return not target.samefile(source)
    except OSError:
        return True


def _mtime(path: Path) -> float:
    """When *path* last changed, now for a path that no longer exists."""
    try:
        return path.lstat().st_mtime
    except FileNotFoundError:
        return time.time()


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
