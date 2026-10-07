"""Path semantics, on-disk guards, and the raw filesystem work of a mutation.

No async and no SQL: this module is the path arithmetic, the HTTP-level
validation every mutation shares (case-insensitive inode aliasing,
parent-chain checks, the upload size limit), and the blocking filesystem
primitives the mutations build from, including the rename journal
(:class:`_Journal`) every change is installed and rolled back through.
"""

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from ..config import settings
from ..entries import ContentStat, is_reserved_path
from ..files import remove_path
from ..humanize import format_bytes
from ..l10n import Localized
from ..store import Casebase
from ..text import NOT_TEXT_REASON

__all__ = [
    "DIRECTORY_PATH_REQUIRED",
    "directory_not_found",
    "document_not_found",
    "file_too_large",
    "no_original",
    "not_text",
]


def document_not_found(path: str) -> Localized[str]:
    return Localized(
        en=f"Document not found: {path}", de=f"Dokument nicht gefunden: {path}"
    )


def no_original(path: str) -> Localized[str]:
    return Localized(
        en=f"No original file found for '{path}'",
        de=f"Keine Originaldatei für „{path}“ gefunden",
    )


DIRECTORY_PATH_REQUIRED = Localized(
    en="Directory path required", de="Ordnerpfad erforderlich"
)


def directory_not_found(path: str) -> Localized[str]:
    return Localized(
        en=f"Directory not found: {path}", de=f"Ordner nicht gefunden: {path}"
    )


def not_text(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' {NOT_TEXT_REASON}", de=f"„{path}“ ist kein textbasierter Inhalt"
    )


def _parent_is_file(path: str) -> Localized[str]:
    return Localized(
        en=f"Destination parent '{path}' is a file",
        de=f"Der übergeordnete Pfad „{path}“ des Ziels ist eine Datei",
    )


_ASSETS_RESERVED = Localized(
    en="'.assets' directories are managed through their owning document",
    de="„.assets“-Ordner werden über ihr zugehöriges Dokument verwaltet",
)


def file_too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"File too large. Maximum size: {format_bytes(limit)}",
        de=f"Datei zu groß. Maximale Größe: {format_bytes(limit)}",
    )


logger = logging.getLogger(__name__)


def _shown(store: Casebase, local: str) -> str:
    """The canonical path a message names, not the one local to the store.

    Every string this package hands back is read by a model or a client that
    addresses a document by its full ``~`` / ``@<group>`` path, so a message
    spelling the store-local one names a path no tool and no route accepts.

    Here rather than beside any one mutation because the rule is the package's,
    not one module's: every message that names a path renders it through this.
    """
    return store.scope.render(local)


def _write_workspace_file(workspace_dir: Path, filepath: str, content: bytes) -> Path:
    """Write a file into the workspace, creating its parent directories."""
    full_path = workspace_dir / filepath
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    return full_path


def _write_markdown_file(
    workspace_dir: Path, filepath: str, content: str
) -> ContentStat | None:
    """Write a markdown projection into the workspace, returning its fingerprint.

    Only the bytes: chunking, embedding, and the SQL rows follow separately.  The
    stat is captured here rather than at index time so it describes exactly the
    bytes that were written — a later touch then reads as a mismatch and earns a
    re-index, instead of being stamped over as already-indexed.  Workspace text
    is always stored as UTF-8, whatever the source encoding was.
    """
    return ContentStat.from_path(
        _write_workspace_file(workspace_dir, filepath, content.encode("utf-8"))
    )


@dataclass(slots=True, frozen=True)
class _WorkspaceChange:
    """One live workspace path and its staged replacement, or removal."""

    relative_path: str
    staged_path: Path | None


def _rmdir_if_empty(directory: Path) -> None:
    with suppress(OSError):
        directory.rmdir()


def _restore(backup: Path, live: Path) -> None:
    live.parent.mkdir(parents=True, exist_ok=True)
    backup.replace(live)


@dataclass(slots=True)
class _Journal:
    """Undo log of the renames one change makes, replayed in reverse to undo it.

    Every step is a rename, so it is cheap whatever it moves, and undoing it
    restores the exact prior inode: a removed path is parked under
    *backup_root* rather than deleted, which must share a filesystem with the
    workspaces it serves (the workspace root does).  Parent directories a step
    had to create are removed again on rollback while they are still empty.
    """

    backup_root: Path
    _undo: list[Callable[[], None]] = field(default_factory=list)
    _parked: int = 0

    def mkdir(self, directory: Path) -> None:
        """Create *directory* and its missing parents, each undone while still empty."""
        missing = [path for path in (directory, *directory.parents) if not path.exists()]
        directory.mkdir(parents=True, exist_ok=True)

        for path in reversed(missing):
            self._undo.append(lambda d=path: _rmdir_if_empty(d))

    def remove(self, live: Path) -> None:
        """Park *live* in the backup root, tolerating its absence."""
        if not live.exists() and not live.is_symlink():
            return

        self._parked += 1
        backup = self.backup_root / str(self._parked)
        backup.parent.mkdir(parents=True, exist_ok=True)
        live.replace(backup)
        self._undo.append(lambda: _restore(backup, live))

    def install(self, staged: Path, live: Path) -> None:
        """Replace *live* with *staged*, parking whatever was there before."""
        self.remove(live)
        self.mkdir(live.parent)
        staged.replace(live)
        self._undo.append(lambda: remove_path(live))

    def rename(self, source: Path, target: Path) -> None:
        """Rename *source* to *target*, which must be free.

        Checked although a plan already did, since a rename onto a file
        silently replaces it.
        """
        if target.exists():
            raise FileExistsError(target)

        self.mkdir(target.parent)
        source.rename(target)
        self._undo.append(lambda: _restore(target, source))

    def apply(self, workspace: Path, change: _WorkspaceChange) -> None:
        """Install one staged change into *workspace*, or remove its path."""
        live = workspace / change.relative_path

        if change.staged_path is None:
            self.remove(live)
        else:
            self.install(change.staged_path, live)

    def rollback(self) -> None:
        """Undo every recorded step, newest first, past any step that fails."""
        while self._undo:
            try:
                self._undo.pop()()
            except OSError:
                logger.exception("Rolling back a workspace change failed")


@contextmanager
def _journaled() -> Generator[tuple[Path, _Journal]]:
    """Yield a staging directory and a journal for one change.

    Every recorded step is rolled back if the block raises.  Both live in a
    temporary directory in the workspace root, so every rename into or out of
    any casebase stays on one filesystem, and the parked backups go with it
    once the change has settled.
    """
    root = Casebase.workspace_root(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix=".stage-", dir=root) as tmp:
        journal = _Journal(Path(tmp) / "backup")

        try:
            yield Path(tmp) / "new", journal
        except BaseException:
            journal.rollback()
            raise


def _is_same_file(a: Path, b: Path) -> bool:
    """Whether *a* and *b* are the same inode.

    True for a case-aliased path on a case-insensitive filesystem (macOS,
    Windows), where ``exists()`` alone cannot distinguish "occupied by another
    file" from "the source under its other spelling".  Missing paths are never
    the same file.
    """
    try:
        return a.samefile(b)
    except OSError:
        return False


def _is_blocked_by_other(target: Path, source: Path) -> bool:
    """Whether *target* exists as a node distinct from *source*.

    A target that aliases *source* (a case-only rename on a case-insensitive
    filesystem) is not a blocker, since a plain rename handles it.
    """
    return target.exists() and not _is_same_file(target, source)


def _check_destination_parents(store: Casebase, target: str) -> None:
    """Reject a destination path whose parent chain is blocked by an existing file.

    Takes the store rather than its directory because the blocker has to be
    named back in the caller's own grammar, and the directory alone cannot say
    which workspace it is.
    """
    workspace_dir = store.workspace_path(settings.data_dir)
    blocker = next(
        (
            parent
            for parent in PurePosixPath(target).parents
            if (workspace_dir / parent).is_file()
        ),
        None,
    )
    if blocker is not None:
        raise HTTPException(
            status_code=409,
            detail=_parent_is_file(_shown(store, str(blocker))).current,
        )


def _check_not_reserved_path(path: str) -> None:
    """Reject paths reaching into a layer the workspace manages for itself."""
    if is_reserved_path(path):
        raise HTTPException(status_code=400, detail=_ASSETS_RESERVED.current)


def _enforce_file_size(content: bytes) -> None:
    """Reject content exceeding the configured maximum upload size."""
    limit = settings.limits.max_file_size_bytes
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=file_too_large(limit).current,
        )
