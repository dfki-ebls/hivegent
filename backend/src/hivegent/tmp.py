"""A conversation's ``/tmp``: its working files, outside every workspace.

Each chat conversation owns one folder, ``<data_dir>/tmp/<conversation_id>/``,
and every agent surface spells it ``/tmp``: the read and write tools, a tool
result too large to show whole, and a sandboxed program, where it is mounted at
``/tmp``.  It lives for the conversation like ``/tmp`` lives for a container
session, is written directly (its root's policy is
:class:`~hivegent.tools.base.Direct`) with no changeset, approval, or store
lock, and is never indexed, searched, or announced.

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
import os
import shutil
import threading
import time
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import cache
from itertools import chain
from pathlib import Path
from stat import S_ISDIR
from weakref import WeakValueDictionary

from .changes import Changeset, CreateDir, Delete, Edit, Move, Write
from .config import sanitize_conversation_id, settings
from .db.conversations import existing_conversation_ids
from .files import atomic_write, remove_path
from .humanize import format_bytes
from .tools.base import Direct, SearchPath, ToolRetry, resolve_accessible_file
from .tools.scope import PrefixScope
from .workspace.changeset import (
    _created_directory,
    _deleted,
    _destination_exists_at,
    _into_itself,
    _moved,
)
from .workspace.documents import derive_text, edit_mutation, write_mutation
from .workspace.paths import _parent_is_file, document_not_found

__all__ = [
    "TMP_SCOPE",
    "DirectPlan",
    "clear_tmp",
    "copy_tmp",
    "plan_direct",
    "remove_tmp",
    "save_result",
    "sweep_tmp",
    "tmp_dir",
    "tmp_search_path",
]

TMP_SCOPE = PrefixScope("/tmp")
"""How every agent surface spells a conversation's folder."""

_RESULTS_DIR = ".tool-results"
"""The folder below ``/tmp`` that holds the tool results too large to show whole."""


_locks: WeakValueDictionary[Path, threading.Lock] = WeakValueDictionary()
_locks_guard = threading.Lock()


@contextmanager
def _locked(roots: Iterable[SearchPath]) -> Generator[None]:
    """Hold the one live lock of each of *roots*, in path order so two callers cannot deadlock."""
    with ExitStack() as stack:
        for path in sorted({root.path for root in roots}):
            with _locks_guard:
                lock = _locks.setdefault(path, threading.Lock())

            _ = stack.enter_context(lock)

        yield


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
    return SearchPath(
        path=tmp_dir(data_dir, conversation_id),
        scope=TMP_SCOPE,
        policy=Direct(settings.tmp.max_bytes, reserved=_RESULTS_DIR),
    )


def _size(path: Path) -> int:
    """The bytes the files at or below *path* hold, none for nothing."""
    if path.is_dir() and not path.is_symlink():
        return sum(
            (directory / name).lstat().st_size
            for directory, _dirs, files in path.walk()
            for name in files
        )

    return path.lstat().st_size if path.exists() or path.is_symlink() else 0


def _canonical(root: SearchPath, path: Path) -> str:
    return root.prefixed(path.relative_to(root.path).as_posix())


def _locate(paths: Sequence[SearchPath], canonical: str) -> tuple[SearchPath, Path]:
    resolved = resolve_accessible_file(tuple(paths), canonical)

    if resolved is None or not isinstance(resolved[0].policy, Direct):
        raise ToolRetry(f"'{canonical}' is not accessible.")

    if resolved[0].is_reserved(resolved[1]):
        raise ToolRetry(
            f"'{canonical}' holds saved tool results, which can be read but not changed."
        )

    return resolved[0], resolved[2]


@dataclass(slots=True, frozen=True)
class DirectPlan:
    """A changeset of direct roots, derived and checked but not written yet."""

    reports: tuple[str, ...] = ()
    deletes: tuple[Path, ...] = ()
    moves: tuple[tuple[Path, Path], ...] = ()
    mkdirs: tuple[Path, ...] = ()
    writes: Mapping[Path, bytes] = field(default_factory=dict)
    roots: Mapping[Path, SearchPath] = field(default_factory=dict)
    """The root of each path above, which bounds and locks it."""
    evictable: Path | None = None
    """A folder whose files may go, oldest first, to keep the writes within the cap."""

    def apply(self) -> tuple[str, ...]:
        """Check the plan again and write it, returning one report per item.

        The checks :func:`plan_direct` made are repeated under the lock of
        every root the plan touches, since another write may have landed in
        between, and the lock is held until the plan is written.  Removals land
        first, then moves, new directories, and whole files, each replaced
        atomically.

        Raises:
            ToolRetry: When a path is of the wrong type, a folder would outgrow
                its cap, or the filesystem refuses a step.
        """
        with _locked(self.roots.values()):
            evicted = _check_quota(self.roots, self.deletes, self.writes, self.evictable)
            deletes = (*self.deletes, *evicted)
            _check_types(self.roots, deletes, self.moves, self.mkdirs, self.writes)

            try:
                for path in deletes:
                    remove_path(path)

                for origin, path in self.moves:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _ = origin.rename(path)

                for path in self.mkdirs:
                    path.mkdir(parents=True, exist_ok=True)

                for path, data in self.writes.items():
                    atomic_write(path, data)

            except OSError as exc:
                raise ToolRetry(f"The change could not be written: {exc.strerror}.") from exc

        return self.reports


def plan_direct(paths: Sequence[SearchPath], changeset: Changeset[str]) -> DirectPlan:
    """Derive and check *changeset* for the direct roots of *paths*, writing nothing.

    No changeset gateway, approval, or store lock: the folder is the run's own
    working state.  Every item is derived here, so a text change that cannot
    apply, a path of the wrong type, or a folder growing past its cap, is
    refused before a caller writes anything, the gated half included.  The
    checks are advisory, and :meth:`DirectPlan.apply` makes them again under
    the lock.  The cap is asked of what the items replace, and the folder is
    only walked whole when they grow it.

    Raises:
        HTTPException: When a text change cannot apply.
        ToolRetry: When a path is missing, taken, or reserved, or a folder
            would outgrow its cap.
    """
    reports: list[str] = []
    deletes: list[Path] = []
    moves: list[tuple[Path, Path]] = []
    mkdirs: list[Path] = []
    writes: dict[Path, bytes] = {}
    roots: dict[Path, SearchPath] = {}

    for op in changeset.operations:
        match op:
            case Write(target=target) | Edit(target=target):
                root, path = _locate(paths, target)
                mutate = (
                    write_mutation(target, op.content, op.mode)
                    if isinstance(op, Write)
                    else edit_mutation(target, op.edits)
                )
                _current, text, report = derive_text(path, target, mutate, op.basis)
                writes[path] = text.encode()
                roots[path] = root
            case Delete(target=target):
                root, path = _locate(paths, target)

                if not (path.exists() or path.is_symlink()):
                    raise ToolRetry(f"'{target}' not found.")

                deletes.append(path)
                roots[path] = root
                report = _deleted(target).current
            case Move(source=source, destination=destination):
                source_root, origin = _locate(paths, source)
                root, path = _locate(paths, destination)
                roots[origin] = source_root

                if path.is_dir():
                    path /= origin.name

                moves.append((origin, path))
                roots[path] = root
                report = _moved(source, _canonical(root, path)).current
            case CreateDir(target=target):
                root, path = _locate(paths, target)
                mkdirs.append(path)
                roots[path] = root
                report = _created_directory(target).current

        reports.append(report)

    _check_types(roots, deletes, moves, mkdirs, writes)
    _ = _check_quota(roots, deletes, writes)

    return DirectPlan(tuple(reports), tuple(deletes), tuple(moves), tuple(mkdirs), writes, roots)


def _probe(path: Path) -> bool | None:
    """Whether *path* is a directory on disk, ``None`` for nothing."""
    try:
        return S_ISDIR(path.stat().st_mode)
    except OSError:
        return None


def _check_types(
    roots: Mapping[Path, SearchPath],
    deletes: Sequence[Path],
    moves: Sequence[tuple[Path, Path]],
    mkdirs: Sequence[Path],
    writes: Iterable[Path],
) -> None:
    """Refuse a path of the wrong type once the items before it applied, writing nothing.

    The items are taken in :meth:`DirectPlan.apply`'s order against the disk
    overlaid with what the earlier ones did.  An overlay entry is the disk
    path whose content now lies there (a moved tree), ``True`` for a new
    directory, ``False`` for a new file, or ``None`` for nothing.
    """
    overlay: dict[Path, Path | bool | None] = {}
    probe = cache(_probe)

    def node(path: Path) -> Path | bool | None:
        for level in (path, *path.parents):
            if level in overlay:
                found = overlay[level]

                if isinstance(found, Path):
                    return found / path.relative_to(level)

                return found if level == path else None

        return path

    def kind(path: Path) -> bool | None:
        found = node(path)

        return probe(found) if isinstance(found, Path) else found

    def put(path: Path, value: Path | bool | None) -> dict[Path, Path | bool | None]:
        """Replace the tree at *path*, returning the overlay entries below it."""
        below = [child for child in overlay if child != path and child.is_relative_to(path)]
        replaced = {child: overlay.pop(child) for child in below}
        overlay[path] = value

        return replaced

    def ensure_dir(root: SearchPath, directory: Path) -> None:
        """Refuse a file at or above *directory* within *root*, creating what is missing."""
        depth = len(directory.relative_to(root.path).parts)

        for level in reversed((directory, *directory.parents)[: depth + 1]):
            match kind(level):
                case False:
                    raise ToolRetry(_parent_is_file(_canonical(root, level)).current)
                case None:
                    overlay[level] = True

    for path in deletes:
        _ = put(path, None)

    for origin, path in moves:
        root = roots[path]

        if kind(origin) is None:
            raise ToolRetry(document_not_found(_canonical(roots[origin], origin)).current)

        if path.is_relative_to(origin):
            raise ToolRetry(_into_itself(_canonical(roots[origin], origin)).current)

        ensure_dir(root, path.parent)

        if kind(path) is not None:
            raise ToolRetry(_destination_exists_at(_canonical(root, path)).current)

        moved = node(origin)
        carried = put(origin, None)
        _ = put(path, moved)
        overlay.update({path / child.relative_to(origin): value for child, value in carried.items()})

    for path in mkdirs:
        ensure_dir(roots[path], path)

    for path in writes:
        ensure_dir(roots[path], path.parent)
        _ = put(path, False)


def _check_quota(
    roots: Mapping[Path, SearchPath],
    deletes: Sequence[Path],
    writes: Mapping[Path, bytes],
    evictable: Path | None = None,
) -> tuple[Path, ...]:
    """Refuse changes that grow a direct root past its cap, returning the files evicted to fit.

    The files in *evictable* other than those written go oldest first until
    the change fits, and a change that still does not is refused.  A folder
    already over its cap may still shrink, so a run can replace its own state
    while it sits at the limit.
    """
    growth: dict[Path, tuple[SearchPath, int]] = {}

    for path in (*deletes, *writes):
        root = roots[path]
        # A path inside a removed tree was counted with the tree.
        covered = any(path != gone and path.is_relative_to(gone) for gone in deletes)
        _root, delta = growth.get(root.path, (root, 0))
        delta += len(writes.get(path, b"")) - (0 if covered else _size(path))
        growth[root.path] = root, delta

    evicted: list[Path] = []

    for root, delta in growth.values():
        if not isinstance(root.policy, Direct):
            continue

        cap = root.policy.max_bytes
        # The folder is only walked when the change grows it.
        excess = _size(root.path) + delta - cap if delta > 0 else 0

        if excess > 0 and evictable is not None and evictable.is_relative_to(root.path):
            for _mtime, size, path in _oldest(evictable, writes):
                if excess <= 0:
                    break

                evicted.append(path)
                excess -= size

        if excess > 0:
            raise ToolRetry(
                f"This would grow {root.prefixed('')} to {format_bytes(cap + excess)}, "
                f"over the {format_bytes(cap)} one conversation may keep there. "
                "Remove what it no longer needs."
            )

    return tuple(evicted)


def _oldest(folder: Path, keep: Mapping[Path, bytes]) -> list[tuple[float, int, Path]]:
    """The files directly in *folder* besides *keep*, oldest first, from one listing."""
    found: list[tuple[float, int, Path]] = []

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)

                if entry.is_file(follow_symlinks=False) and path not in keep:
                    stat = entry.stat(follow_symlinks=False)
                    found.append((stat.st_mtime, stat.st_size, path))

    except FileNotFoundError:
        return []

    return sorted(found)


def save_result(root: SearchPath, name: str, content: bytes) -> str:
    """Save a tool result as *name* in the folder *root* reserves, returning its canonical path.

    Written the way :func:`plan_direct` writes any other change, so the
    folder's cap applies, after dropping the oldest saved results the new one
    would not fit beside: a result can be asked for again, while the run's own
    state cannot.  Only the reserved folder is listed, and only when the cap
    requires it, and the folder is walked once.

    Raises:
        ToolRetry: When *name* is no single filename, *root* is not written
            directly, or the result does not fit even beside no other saved one.
    """
    if name in {"", ".."} or Path(name).name != name:
        raise ToolRetry("A saved result must have a single filename.")

    target = root.prefixed(f"{_RESULTS_DIR}/{name}")

    if not isinstance(root.policy, Direct):
        raise ToolRetry(f"'{target}' is not accessible.")

    folder = root.path / _RESULTS_DIR
    path = folder / name
    _ = DirectPlan(writes={path: content}, roots={path: root}, evictable=folder).apply()

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

    def expire() -> tuple[int, list[Path]]:
        expired, idle = 0, []

        for folder in root.iterdir():
            if not _touched_since(folder, stale):
                remove_path(folder)
                expired += 1
            elif not _touched_since(folder, orphaned):
                idle.append(folder)

        return expired, idle

    expired, idle = await asyncio.to_thread(expire)
    live = await existing_conversation_ids([folder.name for folder in idle])
    orphans = [folder for folder in idle if folder.name not in live]
    await asyncio.to_thread(lambda: [remove_path(folder) for folder in orphans])

    return expired + len(orphans)
