"""Path semantics, on-disk guards, and the raw filesystem work of a mutation.

No async and no SQL: this module is the path arithmetic, the HTTP-level
validation every mutation shares — ``mv`` destination resolution,
case-insensitive inode aliasing, parent-chain checks, the upload size limit —
and the blocking filesystem primitives the mutations build from.  The latter
are deliberately synchronous: each is called while the casebase lock is held,
so callers hand the expensive ones (:func:`_count_files`, :func:`_remove_tree`)
to :func:`asyncio.to_thread` rather than stalling the event loop for as long as
the subtree takes.
"""

import shutil
from contextlib import suppress
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..config import settings
from ..entries import ContentStat, is_assets_dir
from ..humanize import format_bytes

__all__: list[str] = []


def _count_files(directory: Path) -> int:
    """Count the regular files anywhere under *directory*."""
    return sum(1 for path in directory.rglob("*") if path.is_file())


def _remove_tree(directory: Path) -> None:
    """Remove *directory* and everything under it, tolerating its absence.

    Absence is the only tolerated failure, so a subtree that could not be
    removed still raises rather than leaving stale files behind unreported.
    """
    with suppress(FileNotFoundError):
        shutil.rmtree(directory)


def _write_original_file(workspace_dir: Path, filepath: str, content: bytes) -> Path:
    """Write a binary original file into the workspace."""
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
    re-index, instead of being stamped over as already-indexed.
    """
    full_path = workspace_dir / filepath
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return ContentStat.from_path(full_path)


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


def _check_not_assets_path(path: str) -> None:
    """Reject paths that reach into the managed ``.assets`` layer.

    ``.assets`` directories are derived storage owned by their document entry
    and hidden from the directory tree, so creating or renaming one through
    the generic directory/move API would silently strand content the UI can
    never show again.
    """
    if any(is_assets_dir(part) for part in PurePosixPath(path).parts):
        raise HTTPException(
            status_code=400,
            detail="'.assets' directories are managed through their owning document",
        )


def _enforce_file_size(content: bytes) -> None:
    """Reject content exceeding the configured maximum upload size."""
    limit = settings.limits.max_file_size_bytes
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {format_bytes(limit)}",
        )
