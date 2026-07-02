"""Inventory and tree-building helpers for document stores."""

import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ... import workspace
from ...db.documents import list_document_paths
from ...config import settings
from ...converters.base import DOCUMENT_EXTENSION
from ...entries import is_assets_dir
from ...store import Casebase
from ...types import (
    DirectoryEntry,
    DirectoryTreeResponse,
    DocumentInfo,
)

__all__ = ["build_tree_response"]


def _safe_iterdir(dir_path: Path) -> list[Path]:
    """List *dir_path* sorted, tolerating it vanishing mid-scan.

    The inventory endpoints walk the workspace read-only, without the casebase
    lock, so a concurrent locked mutation (a delete or move issuing
    ``rmtree``/``unlink``/``rename``) can remove entries underfoot. Returning an
    empty listing for a vanished directory keeps these reads from raising and
    surfacing as a 500.
    """
    try:
        return sorted(dir_path.iterdir())
    except OSError:
        return []


def _scan_directory(dir_path: Path) -> tuple[list[Path], dict[Path, os.stat_result]]:
    """List *dir_path* once, returning its subdirectories and stat'd files.

    A single listing plus one ``stat`` per child classifies entries into
    subdirectories and ``{regular file: its stat}``; an entry that vanishes
    between listing and stat is dropped (see :func:`_safe_iterdir`). Both tree
    walkers share this so neither re-lists nor re-stats a directory.
    """
    subdirs: list[Path] = []
    file_stats: dict[Path, os.stat_result] = {}
    for item in _safe_iterdir(dir_path):
        try:
            st = item.stat()
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            subdirs.append(item)
        elif stat.S_ISREG(st.st_mode):
            file_stats[item] = st
    return subdirs, file_stats


def _entries_from_files(
    file_stats: Mapping[Path, os.stat_result],
    root_path: Path,
    chunk_counts: dict[str, int],
    hidden_stems: frozenset[str],
) -> list[DocumentInfo]:
    """Group already-stat'd sibling files into logical stem entries."""
    grouped: dict[str, list[Path]] = {}
    for item in file_stats:
        relative = item.relative_to(root_path)
        stem_path = str((relative.parent / item.stem).as_posix())
        grouped.setdefault(stem_path, []).append(item)

    entries: list[DocumentInfo] = []
    for stem_path, files in sorted(grouped.items()):
        # An upload in flight (or its rollback) may have written some of the
        # entry's files already; hide it until the mutation settles.
        if stem_path in hidden_stems:
            continue
        description = next(
            (file for file in files if file.suffix == DOCUMENT_EXTENSION), None
        )
        original = next(
            (file for file in files if file.suffix != DOCUMENT_EXTENSION), None
        )
        primary = original or description
        if primary is None:
            continue

        entry_path = str((description or primary).relative_to(root_path).as_posix())
        modified_at = max(file_stats[file].st_mtime for file in files)
        size_bytes = file_stats[primary].st_size
        entries.append(
            DocumentInfo(
                filename=entry_path,
                display_name=PurePosixPath(stem_path).name,
                size_bytes=size_bytes,
                modified_at=datetime.fromtimestamp(modified_at, tz=UTC),
                chunk_count=chunk_counts.get(entry_path),
                has_original=original is not None,
                original_path=(
                    str(original.relative_to(root_path).as_posix())
                    if original is not None
                    else None
                ),
                assets_dir=(
                    f"{stem_path}.assets"
                    if (root_path / f"{stem_path}.assets").exists()
                    else None
                ),
            )
        )
    return entries


def _build_directory_tree(
    dir_path: Path,
    root_path: Path,
    chunk_counts: dict[str, int],
    hidden_stems: frozenset[str],
) -> DirectoryEntry:
    """Recursively build a logical-entry directory tree."""
    relative = str(dir_path.relative_to(root_path).as_posix())
    name = dir_path.name if dir_path != root_path else ""
    entry_path = relative if relative != "." else ""
    children: list[DirectoryEntry] = []

    subdirs, file_stats = _scan_directory(dir_path)
    for item in subdirs:
        if not is_assets_dir(item.name):
            children.append(
                _build_directory_tree(item, root_path, chunk_counts, hidden_stems)
            )

    for file_entry in _entries_from_files(
        file_stats, root_path, chunk_counts, hidden_stems
    ):
        children.append(
            DirectoryEntry(
                type="file",
                name=file_entry.display_name,
                path=file_entry.filename,
                size_bytes=file_entry.size_bytes,
                modified_at=file_entry.modified_at,
                chunk_count=file_entry.chunk_count,
                has_original=file_entry.has_original,
                original_path=file_entry.original_path,
                assets_dir=file_entry.assets_dir,
            )
        )

    return DirectoryEntry(
        type="directory",
        name=name,
        path=entry_path,
        children=children,
    )


async def build_tree_response(store: Casebase) -> DirectoryTreeResponse:
    """Build a directory tree response for any casebase."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    chunk_counts = await list_document_paths(store)

    root = _build_directory_tree(
        workspace_dir,
        workspace_dir,
        chunk_counts,
        workspace.inflight_stems(store),
    )

    def _count(entry: DirectoryEntry) -> tuple[int, int]:
        """Return the (files, directories) totals of the subtree at *entry*."""
        files = 1 if entry.type == "file" else 0
        directories = 1 if entry.type == "directory" else 0
        for child in entry.children or []:
            child_files, child_directories = _count(child)
            files += child_files
            directories += child_directories
        return files, directories

    # The synthetic root itself is excluded: count only its subtrees.
    total_files = 0
    total_directories = 0
    for child in root.children or []:
        child_files, child_directories = _count(child)
        total_files += child_files
        total_directories += child_directories

    return DirectoryTreeResponse(
        root=root,
        total_files=total_files,
        total_directories=total_directories,
    )
