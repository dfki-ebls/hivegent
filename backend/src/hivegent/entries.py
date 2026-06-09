"""Helpers for logical stem-based workspace entries."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .converters.base import DOCUMENT_EXTENSION, is_markdown_suffix

__all__ = [
    "EntryPaths",
    "asset_ref_for",
    "assets_dir_for_stem",
    "cleanup_empty_parents",
    "description_path_for_stem",
    "entry_exists",
    "find_original_for_reference",
    "is_assets_dir",
    "is_description_file",
    "original_path_for_stem",
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


def is_description_file(rel_path: str) -> bool:
    """Return whether a workspace-relative path is an ingestable description.

    The scratch-versus-document policy seam: only markdown files map to
    logical document entries that the reconciler folds into SQL.  Every
    other on-disk file (originals, store-only assets, and any scratch output
    a future shell tool produces) is inert workspace content that is kept on
    disk but never chunked on its own.

    >>> is_description_file("docs/report.md")
    True
    >>> is_description_file("docs/report.pdf")
    False
    """
    return is_markdown_suffix(PurePosixPath(rel_path).suffix)


def description_path_for_stem(stem_path: str) -> str:
    """Return the markdown description path for a logical stem."""
    return f"{stem_path}{DOCUMENT_EXTENSION}"


def assets_dir_for_stem(stem_path: str) -> str:
    """Return the child-assets directory path for a logical stem."""
    return f"{stem_path}.assets"


def asset_ref_for(assets_dir: str, relpath: str) -> str:
    """Return the in-markdown reference for an extracted asset.

    Assets are referenced relative to the description's sibling ``.assets``
    directory as ``<assets-dir-basename>/<relpath>``, the form that round-trips
    with the reference rewriting in :func:`workspace._replace_image_references`.

    >>> asset_ref_for("docs/report.assets", "img/fig1.png")
    'report.assets/img/fig1.png'
    """
    return str(PurePosixPath(PurePosixPath(assets_dir).name) / relpath)


def original_path_for_stem(stem_path: str, original_ext: str | None) -> str | None:
    """Return the original-file path for a stem, or ``None`` when there is none.

    >>> original_path_for_stem("docs/report", "pdf")
    'docs/report.pdf'
    >>> original_path_for_stem("docs/note", None) is None
    True
    """
    return f"{stem_path}.{original_ext}" if original_ext else None


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


def entry_exists(workspace_dir: Path, reference: str) -> bool:
    """Return whether a logical entry has any workspace files on disk.

    SQL-backed metadata is no longer consulted here — workspace
    presence is the on-disk signal.  Callers needing the SQL view
    should query :mod:`hivegent.db.documents` directly.
    """
    resolved = resolve_entry_paths(workspace_dir, reference)
    if (workspace_dir / resolved.description_path).exists():
        return True
    if resolved.original_path is not None:
        return True
    return resolved.assets_dir is not None


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
