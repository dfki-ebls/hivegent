"""Filesystem primitives shared by the workspace, ``/tmp``, and staged changesets."""

import os
import shutil
import tempfile
import time
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from stat import S_ISDIR

from .config import settings
from .shared.tree import tree

__all__ = [
    "disk_size",
    "remove_path",
    "remove_paths",
    "reset_transient",
    "rmdir_if_empty",
    "touched_since",
    "transient",
]


def transient(*parts: str) -> Path:
    """A path below the folder of the data directory that holds every temporary file of the server, see :func:`reset_transient`."""
    return settings.data_dir.joinpath(".transient", *parts)


def reset_transient() -> None:
    """Empty the transient folder and make it the temporary directory of this process and of the workers it starts.  Blocking.

    Every temporary file of the server lies below, the spooled uploads, the
    staged skills, and the temporary directories of conversions and workers,
    so the boot, which only one server passes, removes whatever a dead one left at once.
    """
    root = transient()
    remove_path(root)
    root.mkdir(parents=True)
    tempfile.tempdir = str(root)
    os.environ["TMPDIR"] = str(root)


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
    return any(_mtime(entry) >= cutoff for entry in tree(path))


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
