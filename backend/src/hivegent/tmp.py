"""A conversation's ``/tmp``: its working files, outside every workspace.

Each chat conversation owns one folder, ``<data_dir>/tmp/<conversation_id>/``,
and every agent surface spells it ``/tmp``: the read and write tools, a tool
result too large to show whole, and a sandboxed program, where it is mounted at
``/tmp``.  It lives for the conversation like ``/tmp`` lives for a container
session, and is never indexed, searched, or announced.

Its changes commit through the one changeset gateway as a
:class:`~hivegent.workspace.operations.Folder`, written directly with no
approval, while this module keeps what is ``/tmp``'s own: the folder's
lifecycle, its size cap, and the saved tool results only the host writes and
the cap may evict.

There is no user level: conversation ids are server-generated keys and the
``conversations`` row names the one user a folder belongs to, so ownership is
read from the database and never from the path.  :func:`tmp_dir` is the one
place an id becomes a path, and it refuses an id that is no safe path segment.

A folder ends three ways: deleting its conversation, the user clearing every
one of theirs (both look the ids up in the database), and :func:`sweep_tmp`,
which also catches what the first two cannot see, a conversation that went with
its user or a turn that never persisted one.
"""

import asyncio
import shutil
import time
from collections.abc import Iterable
from itertools import chain
from pathlib import Path

from .changes import Changeset, Write
from .config import sanitize_conversation_id, settings
from .db.conversations import existing_conversation_ids
from .files import remove_path
from .tools.base import Direct, SearchPath, ToolRetry
from .tools.mutations import mutation_errors
from .tools.scope import PrefixScope
from .workspace import Gateway
from .workspace.locks import _locked
from .workspace.operations import Folder, Quota

__all__ = [
    "TMP_SCOPE",
    "clear_tmp",
    "copy_tmp",
    "remove_tmp",
    "save_result",
    "sweep_tmp",
    "tmp_dir",
    "tmp_root",
    "tmp_search_path",
]

TMP_SCOPE = PrefixScope("/tmp")
"""How every agent surface spells a conversation's folder."""

_RESULTS_DIR = ".tool-results"
"""The folder below ``/tmp`` that holds the tool results too large to show whole."""

_POLICY = Direct(reserved=_RESULTS_DIR)
"""Written directly, the saved results kept for the host."""


def _tmp_root(data_dir: Path) -> Path:
    return data_dir / "tmp"


def tmp_dir(data_dir: Path, conversation_id: str) -> Path:
    """The folder ``/tmp`` names for *conversation_id*, without creating it.

    Raises:
        ValueError: When *conversation_id* is no safe path segment.
    """
    return _tmp_root(data_dir) / sanitize_conversation_id(conversation_id)


def tmp_search_path(data_dir: Path, conversation_id: str) -> SearchPath:
    """The ``/tmp`` root a conversation's run resolves against, written directly."""
    return SearchPath(path=tmp_dir(data_dir, conversation_id), scope=TMP_SCOPE, policy=_POLICY)


def tmp_root(folder: Path) -> Folder:
    """The gateway root that writes *folder* directly, within ``tmp.max_bytes``.

    The host saving a tool result may write the saved results and evict the
    oldest to make room, since a result can be asked for again while the
    run's own state cannot.
    """
    return Folder(folder, TMP_SCOPE, _POLICY, Quota(settings.tmp.max_bytes, _RESULTS_DIR))


async def save_result(folder: Path, name: str, content: str) -> str:
    """Save a tool result as *name* among *folder*'s saved results, returning its canonical path.

    Committed through the gateway like any other ``/tmp`` write, as the host,
    so the cap applies after dropping the oldest saved results the new one
    would not fit beside.

    Raises:
        ToolRetry: When *name* is no single filename, or the result does not
            fit even beside no other saved one.
    """
    if name in {"", ".."} or Path(name).name != name:
        raise ToolRetry("A saved result must have a single filename.")

    target = TMP_SCOPE.render(f"{_RESULTS_DIR}/{name}")

    with mutation_errors(ToolRetry):
        _ = await Gateway((tmp_root(folder),), host=True).apply(
            Changeset((Write(target, content),))
        )

    return target


def _files(folder: Path) -> int:
    return sum(len(files) for _, _, files in folder.walk()) if folder.is_dir() else 0


async def remove_tmp(ids: Iterable[str]) -> None:
    """Delete the folders of the conversations *ids*."""
    folders = [tmp_dir(settings.data_dir, cid) for cid in ids]

    await asyncio.to_thread(lambda: [remove_path(folder) for folder in folders])


async def clear_tmp(ids: Iterable[str]) -> int:
    """Delete the folders of the conversations *ids*, returning how many files they held."""
    folders = [tmp_dir(settings.data_dir, cid) for cid in ids]

    def clear() -> int:
        count = sum(map(_files, folders))

        for folder in folders:
            remove_path(folder)

        return count

    return await asyncio.to_thread(clear)


async def copy_tmp(source: str, target: str) -> None:
    """Give conversation *target* a copy of *source*'s folder.

    A compacted conversation continues its source, whose summary may name the
    scripts and state the source kept in ``/tmp``.
    """
    origin = tmp_dir(settings.data_dir, source)

    if origin.is_dir():
        _ = await asyncio.to_thread(
            shutil.copytree,
            origin,
            tmp_dir(settings.data_dir, target),
            symlinks=True,
            dirs_exist_ok=True,
        )


def _touched_since(path: Path, cutoff: float) -> bool:
    """Whether anything at or below *path* changed after *cutoff*, stopping at the first.

    A file that vanishes while the folder is walked was just in use, so it
    counts as touched rather than failing the sweep of every other folder.
    """
    return any(_mtime(entry) >= cutoff for entry in chain((path,), path.rglob("*")))


def _mtime(path: Path) -> float:
    """When *path* last changed, now for a path that no longer exists."""
    try:
        return path.lstat().st_mtime
    except FileNotFoundError:
        return time.time()


def _remove_if_idle(folder: Path, cutoff: float) -> bool:
    if _touched_since(folder, cutoff):
        return False

    remove_path(folder)

    return True


async def sweep_tmp() -> int:
    """Delete stale and orphaned folders, returning how many went.

    Stale is untouched for ``tmp.ttl_hours``.  Orphaned is naming no
    conversation row, which a user deletion cascades away without asking this
    module, but only once it sat untouched for a sweep interval: a new
    conversation's row is written when its first turn ends, so a folder that
    turn is still using has none yet.  Only those idle folders are looked up.
    """
    root = _tmp_root(settings.data_dir)

    if not root.is_dir():
        return 0

    now = time.time()
    stale = now - settings.tmp.ttl_hours * 3600
    orphaned = now - settings.tmp.sweep_interval_hours * 3600

    def candidates() -> tuple[list[Path], list[Path]]:
        expired: list[Path] = []
        idle: list[Path] = []

        for folder in root.iterdir():
            if not _touched_since(folder, stale):
                expired.append(folder)
            elif not _touched_since(folder, orphaned):
                idle.append(folder)

        return expired, idle

    expired, idle = await asyncio.to_thread(candidates)
    live = await existing_conversation_ids([folder.name for folder in idle])
    orphans = [folder for folder in idle if folder.name not in live]
    removed = 0

    for folder, cutoff in chain(
        ((folder, stale) for folder in expired),
        ((folder, orphaned) for folder in orphans),
    ):
        async with _locked(tmp_root(folder)):
            removed += await asyncio.to_thread(_remove_if_idle, folder, cutoff)

    return removed
