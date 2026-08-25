"""Path semantics, on-disk guards, and the raw filesystem work of a mutation.

No async and no SQL: this module is the path arithmetic, the HTTP-level
validation every mutation shares — ``mv`` destination resolution,
case-insensitive inode aliasing, parent-chain checks, the upload size limit —
and the blocking filesystem primitives the mutations build from.
"""

import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..config import settings
from ..entries import SCRATCH_DIR_NAME, ContentStat, is_assets_dir, is_scratch_path
from ..humanize import format_bytes

__all__: list[str] = []


def _remove_tree(directory: Path) -> None:
    """Remove *directory* and everything under it, tolerating its absence.

    Absence is the only tolerated failure, so a subtree that could not be
    removed still raises rather than leaving stale files behind unreported.
    """
    with suppress(FileNotFoundError):
        shutil.rmtree(directory)


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


def _remove_workspace_path(path: Path) -> None:
    """Remove one workspace path, whether it is a file or directory."""
    if path.is_dir():
        _remove_tree(path)
    else:
        path.unlink(missing_ok=True)


@contextmanager
def _replace_workspace_paths(
    workspace: Path,
    backup_root: Path,
    changes: Sequence[_WorkspaceChange],
) -> Iterator[None]:
    """Install staged paths and restore every prior path if installation fails."""
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for change in changes:
            live = workspace / change.relative_path
            if not live.exists() and not live.is_symlink():
                continue
            backup = backup_root / change.relative_path
            backup.parent.mkdir(parents=True, exist_ok=True)
            live.replace(backup)
            backups.append((live, backup))

        for change in changes:
            if change.staged_path is None:
                continue
            live = workspace / change.relative_path
            live.parent.mkdir(parents=True, exist_ok=True)
            change.staged_path.replace(live)
            installed.append(live)

        yield
    except BaseException:
        for live in reversed(installed):
            _remove_workspace_path(live)
        for live, backup in reversed(backups):
            live.parent.mkdir(parents=True, exist_ok=True)
            backup.replace(live)
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


def _resolve_move_destination(
    workspace_dir: Path, src_name: str, dst: str, src_path: Path
) -> str:
    """Apply ``mv`` semantics: an existing-directory destination means move into it.

    The source itself is exempt: on a case-insensitive filesystem the
    destination of a case-only rename aliases the source and must stay a plain
    rename instead of nesting the source inside itself.
    """
    dst_path = workspace_dir / dst
    if not dst or (dst_path.is_dir() and not _is_same_file(dst_path, src_path)):
        return str(PurePosixPath(dst) / src_name)
    return dst


def _check_destination_parents(workspace_dir: Path, target: str) -> None:
    """Reject a destination path whose parent chain is blocked by an existing file."""
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
            status_code=409, detail=f"Destination parent '{blocker}' is a file"
        )


def _check_not_reserved_path(path: str) -> None:
    """Reject paths reaching into a layer the workspace manages for itself.

    Two reserved directory names, one rule, so a caller cannot land content in
    either by the generic create/move/upload API and have it silently disowned:
    an ``.assets`` payload belongs to its document entry, and a ``.scratch``
    directory is agent state that is never indexed and is wiped at the next
    boot.  Both are hidden from the tree, so content placed there through this
    API would either be unshowable or destroyed.  The write tools reach scratch
    on their own path, which is the one way it is meant to be written.
    """
    if any(is_assets_dir(part) for part in PurePosixPath(path).parts):
        raise HTTPException(
            status_code=400,
            detail="'.assets' directories are managed through their owning document",
        )

    if is_scratch_path(path):
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{SCRATCH_DIR_NAME}' holds agent scratch state, is never "
                "indexed, and is cleared on restart; write it with the document "
                "tools or choose another path"
            ),
        )


def _enforce_file_size(content: bytes) -> None:
    """Reject content exceeding the configured maximum upload size."""
    limit = settings.limits.max_file_size_bytes
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {format_bytes(limit)}",
        )
