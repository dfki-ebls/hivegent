"""Inventory and tree-building helpers for document stores."""

import asyncio
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from cbrkit.io import FOLDER_SUFFIXES, is_casebase

from ...db.documents import list_document_paths
from ...store import Workspace
from ...tools.entries import (
    CASE_BASE_KINDS,
    Part,
    is_assets_dir,
    is_case_base_file,
)
from ...types import DirectoryEntry, DirectoryTreeResponse

__all__ = ["build_tree_response"]


def _safe_iterdir(dir_path: Path) -> list[Path]:
    """List *dir_path* sorted, tolerating it vanishing mid-scan.

    The inventory endpoints walk the workspace read-only, without the workspace
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
    subdir_names: frozenset[str],
) -> list[DirectoryEntry]:
    """Group already-stat'd sibling files into logical file entries.

    *subdir_names* is the sibling directory listing the caller already holds, so
    an entry's ``.assets`` payload is detected in memory rather than costing one
    ``exists`` syscall per entry on every tree read.
    """
    grouped: dict[str, list[tuple[Part, Path]]] = {}

    for item in file_stats:
        part = Part.of(item.relative_to(root_path).as_posix())

        # The cbrkit companions belong to their case base and are never listed.
        if part.kind not in CASE_BASE_KINDS:
            grouped.setdefault(part.stem_path, []).append((part, item))

    entries: list[DirectoryEntry] = []
    for stem_path, files in sorted(grouped.items()):
        display_name = PurePosixPath(stem_path).name
        description = next(
            (file for part, file in files if part.kind == "description"), None
        )
        original = next(
            (file for part, file in files if part.kind != "description"), None
        )
        primary = original or description
        if primary is None:
            continue

        entry_path = str((description or primary).relative_to(root_path).as_posix())
        modified_at = max(file_stats[file].st_mtime for _, file in files)
        size_bytes = file_stats[primary].st_size
        entries.append(
            DirectoryEntry(
                type="file",
                name=display_name,
                path=entry_path,
                size_bytes=size_bytes,
                modified_at=datetime.fromtimestamp(modified_at, tz=UTC),
                chunk_count=chunk_counts.get(entry_path),
                has_original=original is not None,
                case_base_candidate=original is not None
                and is_case_base_file(original.name),
                original_path=(
                    str(original.relative_to(root_path).as_posix())
                    if original is not None
                    else None
                ),
                assets_dir=(
                    Part(stem_path, "assets").path
                    if Part(display_name, "assets").path in subdir_names
                    else None
                ),
            )
        )
    return entries


def _build_directory_tree(
    dir_path: Path,
    root_path: Path,
    chunk_counts: dict[str, int],
) -> DirectoryEntry:
    """Recursively build a logical-entry directory tree.

    A folder may be a case base when it holds a member as cbrkit's `is_casebase` counts them,
    told from the children the walk already built, and asked of cbrkit only for an ``.assets``
    payload the tree leaves out.
    """
    relative = str(dir_path.relative_to(root_path).as_posix())
    name = dir_path.name if dir_path != root_path else ""
    entry_path = relative if relative != "." else ""
    children: list[DirectoryEntry] = []

    subdirs, file_stats = _scan_directory(dir_path)
    parts = {item: Part.of(item.name) for item in file_stats}
    payloads: list[Path] = []
    candidate = any(
        not item.name.startswith(".")
        and part.kind not in CASE_BASE_KINDS
        and item.suffix.lower() in FOLDER_SUFFIXES
        for item, part in parts.items()
    )

    for item in subdirs:
        if is_assets_dir(item.name):
            payloads.append(item)
            continue

        child = _build_directory_tree(item, root_path, chunk_counts)
        children.append(child)
        candidate |= child.case_base_candidate and not item.name.startswith(".")

    children.extend(
        _entries_from_files(
            file_stats,
            root_path,
            chunk_counts,
            frozenset(item.name for item in subdirs),
        )
    )

    return DirectoryEntry(
        type="directory",
        name=name,
        path=entry_path,
        case_base_candidate=candidate
        or any(is_casebase(item) for item in payloads if not item.name.startswith(".")),
        children=children,
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


def _build_tree(
    workspace_dir: Path,
    chunk_counts: dict[str, int],
) -> DirectoryTreeResponse:
    """Walk *workspace_dir* into a tree response.  Blocking; runs off the loop."""
    root = _build_directory_tree(workspace_dir, workspace_dir, chunk_counts)
    total_files, total_directories = _count(root)
    return DirectoryTreeResponse(
        root=root,
        total_files=total_files,
        # The synthetic root itself is excluded: count only its subtrees.
        total_directories=total_directories - 1,
    )


async def build_tree_response(store: Workspace) -> DirectoryTreeResponse:
    """Build a directory tree response for any workspace.

    The walk is one blocking pass of ``iterdir`` + ``stat`` over the entire
    workspace, so it runs in a worker thread rather than stalling the event loop
    for its full duration.  Nothing in it is thread-affine: the walk is pure
    filesystem reads.
    """
    return await asyncio.to_thread(
        _build_tree,
        store.workspace_dir(),
        await list_document_paths(store),
    )
