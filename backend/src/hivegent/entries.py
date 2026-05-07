"""Helpers for logical stem-based workspace entries."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import settings
from .converters.base import DOCUMENT_EXTENSION
from .store import Casebase

__all__ = [
    "EntryPaths",
    "assets_dir_for_stem",
    "cleanup_empty_parents",
    "description_path_for_stem",
    "entry_exists",
    "find_original_for_reference",
    "is_assets_dir",
    "metadata_path_for_reference",
    "resolve_entry_paths",
    "stem_display_name",
    "stem_path_from_reference",
]


@dataclass(slots=True, frozen=True)
class EntryPaths:
    """Resolved paths for a logical stem entry."""

    stem_path: str
    description_path: str
    original_path: str | None
    assets_dir: str | None


def stem_path_from_reference(reference: str) -> str:
    """Return the logical stem path for a workspace-relative reference."""
    pure = PurePosixPath(reference)
    if pure.suffix:
        return str((pure.parent / pure.stem).as_posix())
    return str(pure.as_posix())


def is_assets_dir(name: str) -> bool:
    """Return whether a directory name is a child-assets directory."""
    return name.endswith(".assets")


def description_path_for_stem(stem_path: str) -> str:
    """Return the markdown description path for a logical stem."""
    return f"{stem_path}{DOCUMENT_EXTENSION}"


def assets_dir_for_stem(stem_path: str) -> str:
    """Return the child-assets directory path for a logical stem."""
    return f"{stem_path}.assets"


def metadata_path_for_reference(store: Casebase, reference: str) -> Path:
    """Return the metadata JSON path for a logical entry reference.

    Pure path computation — does not create any directories.  Callers
    that need to *write* the metadata file should call ``mkdir`` on the
    returned path's parent themselves.
    """
    metadata_dir = store.metadata_path(settings.data_dir)
    stem_path = stem_path_from_reference(reference)
    return metadata_dir / f"{stem_path}.json"


def find_original_for_reference(workspace_dir: Path, reference: str) -> str | None:
    """Return the workspace-relative original path for a logical entry."""
    stem_path = stem_path_from_reference(reference)
    stem_pure = PurePosixPath(stem_path)
    parent_dir = workspace_dir / stem_pure.parent
    if not parent_dir.exists():
        return None

    candidates = sorted(
        candidate
        for candidate in parent_dir.iterdir()
        if candidate.is_file()
        and candidate.name != f"{stem_pure.name}{DOCUMENT_EXTENSION}"
        and candidate.stem == stem_pure.name
    )
    if not candidates:
        return None
    return str(candidates[0].relative_to(workspace_dir).as_posix())


def resolve_entry_paths(workspace_dir: Path, reference: str) -> EntryPaths:
    """Resolve logical entry paths from any workspace-relative reference."""
    stem_path = stem_path_from_reference(reference)
    assets_dir = assets_dir_for_stem(stem_path)
    assets_full = workspace_dir / assets_dir
    return EntryPaths(
        stem_path=stem_path,
        description_path=description_path_for_stem(stem_path),
        original_path=find_original_for_reference(workspace_dir, stem_path),
        assets_dir=assets_dir
        if assets_full.exists() and assets_full.is_dir()
        else None,
    )


def entry_exists(workspace_dir: Path, metadata_dir: Path, reference: str) -> bool:
    """Return whether a logical entry exists in workspace or metadata."""
    resolved = resolve_entry_paths(workspace_dir, reference)
    meta_path = metadata_dir / f"{resolved.stem_path}.json"
    if (workspace_dir / resolved.description_path).exists():
        return True
    if resolved.original_path is not None:
        return True
    if resolved.assets_dir is not None:
        return True
    return meta_path.exists()


def stem_display_name(stem_path: str) -> str:
    """Return the user-facing basename for a logical stem."""
    return PurePosixPath(stem_path).name


def cleanup_empty_parents(path: Path, stop_at: Path) -> None:
    """Remove empty parent directories of *path* up to ``stop_at``."""
    parent = path.parent
    while parent != stop_at:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
