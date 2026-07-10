"""Pure path semantics and on-disk guards for workspace mutations.

No async and no SQL: this module is just the path arithmetic and the
HTTP-level validation that every mutation shares — ``mv`` destination
resolution, case-insensitive inode aliasing, parent-chain checks, and the
upload size limit.
"""

from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..config import settings
from ..entries import is_assets_dir
from ..humanize import format_bytes

__all__: list[str] = []


def _write_original_file(workspace_dir: Path, filepath: str, content: bytes) -> Path:
    """Write a binary original file into the workspace."""
    full_path = workspace_dir / filepath
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    return full_path


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
