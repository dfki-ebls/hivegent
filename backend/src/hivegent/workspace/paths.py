"""Path semantics, on-disk guards, and the raw filesystem work of a mutation.

No async and no SQL: this module is the path arithmetic, the HTTP-level
validation every mutation shares (the upload size limit), and the blocking
filesystem primitives the mutations build from, including the rename journal
(:class:`_Journal`) every change is installed and rolled back through, and
which the boot replays for a change a crash interrupted.
"""

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, BinaryIO, Literal, Self

from cbrkit.io import atomic_write, durable_rename, is_partial, sync_directory
from exar.text import NOT_TEXT_REASON
from pydantic import Field, TypeAdapter, ValidationError

from ..config import settings
from ..db import changes as db_changes
from ..files import remove_path, rmdir_if_empty
from ..shared.l10n import Localized, format_bytes
from ..tools.changes import ChangeRefused
from ..tools.entries import ContentStat
from .operations import Root

__all__ = [
    "DIRECTORY_PATH_REQUIRED",
    "already_exists",
    "directory_not_found",
    "document_not_found",
    "file_too_large",
    "kept_changes",
    "no_original",
    "not_text",
    "recover_changesets",
    "sweep_partials",
]


def already_exists(path: str) -> Localized[str]:
    return Localized(en=f"'{path}' already exists", de=f"„{path}“ existiert bereits")


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


def file_too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"File too large. Maximum size: {format_bytes(limit)}",
        de=f"Datei zu groß. Maximale Größe: {format_bytes(limit)}",
    )


logger = logging.getLogger(__name__)

_STAGE_PREFIX = ".stage-"
"""How a change's staging directory beside the roots it changes is named."""


def _shown(root: Root, local: str) -> str:
    """The canonical path a message names, not the one local to the store.

    Every string this package hands back is read by a model or a client that
    addresses a document by its full ``~`` / ``@<group>`` path, so a message
    spelling the store-local one names a path no tool and no route accepts.

    Here rather than beside any one mutation because the rule is the package's,
    not one module's: every message that names a path renders it through this.
    """
    return root.mount.render(local)


def _write_workspace_file(
    workspace_dir: Path, filepath: str, content: bytes, *, sync_folder: bool = True
) -> Path:
    """Write a file into the workspace atomically and durably, creating its parent directories.

    A staged file skips the sync of its folder, since the rename that installs it syncs both folders.
    """
    full_path = workspace_dir / filepath
    atomic_write(full_path, content, sync_folder=sync_folder)

    return full_path


def _write_markdown_file(
    workspace_dir: Path, filepath: str, content: str, *, sync_folder: bool = True
) -> ContentStat | None:
    """Write a markdown projection into the workspace, returning its fingerprint.

    Only the bytes: chunking, embedding, and the SQL rows follow separately.  The
    stat is captured here rather than at index time so it describes exactly the
    bytes that were written, so a later touch then reads as a mismatch and earns a
    re-index, instead of being stamped over as already-indexed.  Workspace text
    is always stored as UTF-8, whatever the source encoding was.
    """
    return ContentStat.from_path(
        _write_workspace_file(
            workspace_dir, filepath, content.encode("utf-8"), sync_folder=sync_folder
        )
    )


@dataclass(slots=True, frozen=True)
class _WorkspaceChange:
    """One live workspace path and its staged replacement, or removal."""

    relative_path: str
    staged_path: Path | None


@dataclass(slots=True, frozen=True)
class _Begin:
    """A journal's first record, naming the staging area of its change."""

    staging: Path
    kind: Literal["begin"] = "begin"


@dataclass(slots=True, frozen=True)
class _Rmdir:
    """Undoes a created directory while it is still empty."""

    path: Path
    kind: Literal["rmdir"] = "rmdir"

    def undo(self) -> None:
        rmdir_if_empty(self.path)


@dataclass(slots=True, frozen=True)
class _Restore:
    """Undoes the rename of *live* to *backup*, once it happened.

    It never replaces what took *live* meanwhile, so a rollback retried at
    boot cannot overwrite a later change.
    """

    backup: Path
    live: Path
    kind: Literal["restore"] = "restore"

    def undo(self) -> None:
        if not os.path.lexists(self.backup):
            return

        if os.path.lexists(self.live):
            raise FileExistsError(self.live)

        self.live.parent.mkdir(parents=True, exist_ok=True)

        # Gone when an earlier attempt moved it and stopped before recording that.
        with suppress(FileNotFoundError):
            durable_rename(self.backup, self.live)


@dataclass(slots=True, frozen=True)
class _Discard:
    """Undoes the installation of the file or tree *inode* at *path*, and only it."""

    path: Path
    inode: int
    kind: Literal["discard"] = "discard"

    def undo(self) -> None:
        if not os.path.lexists(self.path):
            return

        if self.path.lstat().st_ino != self.inode:
            raise FileExistsError(self.path)

        remove_path(self.path)
        sync_directory(self.path.parent)


@dataclass(slots=True, frozen=True)
class _Undone:
    """Records that the newest step still to undo was undone."""

    kind: Literal["undone"] = "undone"


@dataclass(slots=True, frozen=True)
class _Prepared:
    """Records the transaction of the change's rows, written in it before it commits, so its status decides the change."""

    xid: str
    kind: Literal["prepared"] = "prepared"


@dataclass(slots=True, frozen=True)
class _Committed:
    """Records that the change is kept for good, so what is left is to drop its staging area."""

    kind: Literal["committed"] = "committed"


type _Step = _Rmdir | _Restore | _Discard

type _Record = _Begin | _Step | _Undone | _Prepared | _Committed

_RECORD: TypeAdapter[_Record] = TypeAdapter(
    Annotated[_Record, Field(discriminator="kind")]
)


def _journals() -> Path:
    """Where the journal of every change that can still be undone lies."""
    return settings.data_dir / ".journals"


@dataclass(slots=True)
class _Journal:
    """Undo log of the renames one change makes, replayed in reverse to undo it.

    Every step is a rename, so it is cheap whatever it moves, and undoing it
    restores the exact prior inode: a removed path is parked under the
    *staging* area rather than deleted, which must share a filesystem with the
    workspaces it serves (the workspace root does).  Parent directories a step
    had to create are removed again on rollback while they are still empty.

    The log names the staging area (:meth:`begin`) before anything is staged,
    so no staging area outlives a crash unnamed.  Each step's undo is appended
    to :attr:`log` and flushed before the step, and the folders the steps
    changed are flushed once at :meth:`commit`, which records the change as
    kept, so a change a crash or a power loss interrupted before is rolled back
    at the next boot (:func:`recover_changesets`), and every undo is recorded
    once done, so a rollback that stopped is resumed rather than repeated.  A
    change whose rows committed is kept by the boot too, since the journal
    names their transaction (:meth:`prepare`) and PostgreSQL knows whether it
    committed (:mod:`hivegent.db.changes`).  The log goes after
    the staging area once the change committed or was undone, and a rollback
    that stopped renames it to :attr:`kept` (:func:`kept_changes`).
    """

    staging: Path
    _steps: list[_Step] = field(default_factory=list[_Step])
    _parked: int = 0
    _dirty: set[Path] = field(default_factory=set[Path])
    _file: BinaryIO | None = None
    committed: bool = False
    """Whether the change is kept for good, so neither a rollback nor a boot undoes it."""
    xid: str | None = None
    """The transaction of the change's rows, once :meth:`prepare` named it."""

    @property
    def log(self) -> Path:
        """The file the undo of every step is appended to."""
        # Named apart from the staging areas, which :func:`_sweep_staging` takes for leftovers.
        return _journals() / f"{self.staging.name.removeprefix(_STAGE_PREFIX)}.jsonl"

    @property
    def kept(self) -> Path:
        """Where :attr:`log` lies once a rollback stopped."""
        return self.log.with_suffix(".kept")

    @classmethod
    def load(cls, log: Path) -> Self | None:
        """The journal *log* recorded, ``None`` when it recorded no step.

        A crash can only tear the last record, written ahead of a step that
        therefore never ran, so that one is dropped.

        Raises:
            ValidationError: When a record before the last one is invalid.
        """
        lines = log.read_bytes().splitlines()
        journal: Self | None = None

        for number, line in enumerate(lines, 1):
            try:
                record = _RECORD.validate_json(line)
            except ValidationError:
                if number < len(lines):
                    raise

                break

            match record:
                case _Begin(staging=staging):
                    journal = cls(staging)
                case _Undone() if journal is not None:
                    _ = journal._steps.pop()
                case _Prepared(xid=xid) if journal is not None:
                    journal.xid = xid
                case _Committed() if journal is not None:
                    journal._steps.clear()
                    journal.committed = True
                case _Rmdir() | _Restore() | _Discard() if journal is not None:
                    journal._steps.append(record)
                case _:
                    raise ValueError(f"{log} records a step before its staging area")

        return journal

    def begin(self) -> None:
        """Open the log, naming the staging area durably before anything is staged in it."""
        _ = self._open()

    def _open(self) -> BinaryIO:
        if self._file is None:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.log.open("ab")

            if self._file.tell() == 0:
                _ = self._file.write(_RECORD.dump_json(_Begin(self.staging)) + b"\n")
                self._file.flush()
                os.fsync(self._file.fileno())
                sync_directory(self.log.parent)

        return self._file

    def _write(self, record: _Step | _Undone | _Prepared | _Committed) -> None:
        file = self._open()
        _ = file.write(_RECORD.dump_json(record) + b"\n")
        file.flush()
        os.fsync(file.fileno())

    def _release(self) -> None:
        """Close the log."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def _step(self, step: _Step) -> None:
        """Log *step* ahead of the change it undoes."""
        self._write(step)
        self._steps.append(step)

    def _move(self, source: Path, target: Path) -> None:
        """Rename *source* to *target*, replacing it, and leave both folders to flush at commit."""
        _ = source.replace(target)
        self._dirty.update((source.parent, target.parent))

    def mkdir(self, directory: Path) -> None:
        """Create *directory* and its missing parents, each undone while still empty."""
        missing = [
            path for path in (directory, *directory.parents) if not path.exists()
        ]

        for path in reversed(missing):
            self._step(_Rmdir(path))

        directory.mkdir(parents=True, exist_ok=True)
        self._dirty.update(path.parent for path in missing)

    def remove(self, live: Path) -> None:
        """Park *live* in the staging area, tolerating its absence."""
        if not live.exists() and not live.is_symlink():
            return

        self._parked += 1
        backup = self.staging / "backup" / str(self._parked)

        backup.parent.mkdir(parents=True, exist_ok=True)
        self._dirty.update((self.staging, self.staging.parent))
        self._step(_Restore(backup, live))
        self._move(live, backup)

    def install(self, staged: Path, live: Path) -> None:
        """Replace *live* with *staged*, parking whatever was there before."""
        self.remove(live)
        self.mkdir(live.parent)
        self._step(_Discard(live, staged.lstat().st_ino))
        self._move(staged, live)

    def rename(self, source: Path, target: Path) -> None:
        """Rename *source* to *target*, which must be free.

        Checked although a plan already did, since a rename onto a file
        silently replaces it.
        """
        if target.exists():
            raise FileExistsError(target)

        self.mkdir(target.parent)
        self._step(_Restore(target, source))
        self._move(source, target)

    def apply(self, workspace: Path, change: _WorkspaceChange) -> None:
        """Install one staged change into *workspace*, or remove its path."""
        live = workspace / change.relative_path

        if change.staged_path is None:
            self.remove(live)
        else:
            self.install(change.staged_path, live)

    def _flush(self) -> None:
        """Flush the folders the steps changed, so no record outruns the renames it keeps."""
        for directory in self._dirty:
            with suppress(FileNotFoundError):
                sync_directory(directory)

        self._dirty.clear()

    def prepare(self, xid: str) -> None:
        """Name the transaction of the change's rows, from inside it before it commits.

        The change then stands or falls with that transaction, which a boot asks
        PostgreSQL about when it finds the journal without a commit record.
        """
        self._flush()
        self._write(_Prepared(xid))
        self.xid = xid

    def commit(self) -> None:
        """Keep the change for good, so neither a rollback nor a boot undoes it."""
        self._flush()
        self._write(_Committed())
        self.settle()

    def settle(self) -> None:
        """Take the change as kept, whose rows committed, so only its staging area is left to drop."""
        self._steps.clear()
        self.committed = True

    def rollback(self) -> None:
        """Undo every step newest first, stopping at the first that fails.

        The steps left keep their order in the log, so the next boot resumes
        exactly there (:func:`recover_changesets`).
        """
        recording = True

        while self._steps:
            try:
                self._steps[-1].undo()
            except OSError:
                logger.exception("Rolling back a workspace change failed")
                return

            _ = self._steps.pop()

            # The undos are idempotent, so a step left unrecorded is merely replayed.
            if recording:
                try:
                    self._write(_Undone())
                except OSError:
                    logger.warning("Recording an undo failed", exc_info=True)
                    recording = False

    def close(self) -> bool:
        """Remove the staging area and the log, unless steps are left to undo, as a stopped or undecided rollback leaves them.

        Those keep both, since the staging area holds what they restore.

        Returns:
            Whether the change is settled, committed or undone in full.
        """
        self._release()

        if self._steps:
            logger.error(
                "Kept %s with the journal %s, whose change is not settled: "
                "the next boot settles it",
                self.staging,
                self.log,
            )

            _ = self.log.replace(self.kept)
            sync_directory(self.log.parent)

            return False

        remove_path(self.staging)
        self.log.unlink(missing_ok=True)

        return True


def _logs() -> list[Path]:
    """The journals a crashed server left, a stopped rollback's taken up again."""
    return sorted(
        log.replace(log.with_suffix(".jsonl")) if log.suffix == ".kept" else log
        for log in _journals().glob("*")
        if log.is_file()
    )


def _prepared() -> set[str]:
    """The transactions the undecided journals name, whose status decides them.

    A journal that cannot be read names none, and `_recover` keeps it.
    """
    xids: set[str] = set()

    for log in _logs():
        with suppress(OSError, ValueError):
            journal = _Journal.load(log)

            if (
                journal is not None
                and not journal.committed
                and journal.xid is not None
            ):
                xids.add(journal.xid)

    return xids


def _recover(statuses: Mapping[str, bool | None]) -> None:
    """Settle every journal a crashed server left.

    A change it recorded as kept, or whose rows' transaction committed by
    *statuses*, is kept, and every other one rolled back, except one whose
    transaction PostgreSQL no longer knows, which is kept for the operator.
    """
    for log in _logs():
        try:
            journal = _Journal.load(log)
        except OSError, ValueError:
            logger.exception("Cannot read the journal %s, keeping it", log)
            continue

        if journal is None:
            log.unlink()
            continue

        match journal.committed or (
            journal.xid is not None and statuses.get(journal.xid)
        ):
            case True:
                logger.warning(
                    "Finishing the committed change staged in %s", journal.staging
                )
                journal.settle()
            case False:
                logger.warning(
                    "Rolling back the interrupted change staged in %s", journal.staging
                )
                journal.rollback()
            case None:
                logger.error(
                    "PostgreSQL no longer knows whether the change staged in %s committed, keeping it",
                    journal.staging,
                )

        _ = journal.close()


async def recover_changesets() -> None:
    """Settle every change a crashed server interrupted, as the boot does before anything else.

    One server runs at a time (:func:`~hivegent.db.engine.serving`), so every
    journal found then is one a crash interrupted.  A change whose rows
    committed is kept and every other rolled back.  A journal that cannot be read, undone in full, or
    decided keeps its staging area for the operator, and is one of :func:`kept_changes`.
    The staging areas the crash left go too, while the housekeeping sweeps its
    partial files later (:func:`sweep_partials`), which no change reads.
    """
    statuses = {
        xid: await db_changes.committed(xid)
        for xid in await asyncio.to_thread(_prepared)
    }
    await asyncio.to_thread(_recover, statuses)

    await asyncio.to_thread(_sweep_staging)


_PARTIAL_GRACE = 3600.0
"""Seconds a partial file is left alone, since a younger one may be a write in progress."""


def sweep_partials(data: Path, now: float | None = None) -> int:
    """Remove the partial files of interrupted writes (:func:`cbrkit.io.is_partial`) anywhere below *data*.  Blocking.

    Every write of the data directory goes through `cbrkit.io.atomic_write`,
    so one sweep covers workspaces, mirrors, conversations, and libraries
    alike.  Symlinks are never followed, and a file younger than
    `_PARTIAL_GRACE` is kept, so it runs safely beside the writes in progress.

    Returns:
        The number of files removed.
    """
    cutoff = (time.time() if now is None else now) - _PARTIAL_GRACE
    removed = 0

    for dir_path, _dir_names, file_names in os.walk(data):
        for name in file_names:
            path = Path(dir_path, name)

            with suppress(FileNotFoundError):
                if is_partial(name) and path.lstat().st_mtime < cutoff:
                    logger.warning(
                        "Removing %s, the partial file of an interrupted write", path
                    )
                    path.unlink()
                    removed += 1

    return removed


def _sweep_staging() -> None:
    """Remove the staging areas beside the data directory's roots that no journal names.

    A journal names its staging area before anything is staged there and goes
    after it, so one no journal names was left by a crash
    and holds nothing to restore.  Listed before the journals are read, so a
    change starting meanwhile is named by then.  Nothing is swept while a
    journal cannot be read, since it might name one.
    """
    data = settings.data_dir
    stages = [
        *data.glob(f"{_STAGE_PREFIX}*"),
        *data.glob(f"*/{_STAGE_PREFIX}*"),
    ]
    named: set[Path] = set()

    for log in (*_journals().glob("*.jsonl"), *_journals().glob("*.kept")):
        try:
            journal = _Journal.load(log)
        except FileNotFoundError:
            continue
        except OSError, ValueError:
            return

        if journal is not None:
            named.add(journal.staging)

    for stage in stages:
        if stage not in named:
            logger.warning(
                "Removing the staging area %s, which no journal names", stage
            )
            remove_path(stage)


def kept_changes() -> list[Path]:
    """The journals of changes whose rollback could not finish or whose outcome is unknown, or that cannot be read.

    Their staging areas hold parked files, which a reconciliation would take
    for deleted, so it is refused while any is left.  A journal of a change
    in flight is none of them.
    """
    kept = sorted(_journals().glob("*.kept"))

    for log in sorted(_journals().glob("*.jsonl")):
        try:
            _ = _Journal.load(log)
        except FileNotFoundError:
            continue
        except OSError, ValueError:
            kept.append(log)

    return kept


def _enforce_file_size(content: bytes) -> None:
    """Reject content exceeding the configured maximum upload size."""
    limit = settings.limits.max_file_size_bytes
    if len(content) > limit:
        raise ChangeRefused(
            status_code=413,
            detail=file_too_large(limit).current,
        )
