"""Inventory and tree-building helpers for document stores."""

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ...db.documents import list_document_paths
from ...config import settings
from ...converters.base import DOCUMENT_EXTENSION
from ...entries import is_assets_dir
from ...store import Casebase
from ...types import (
    DirectoryEntry,
    DirectoryTreeResponse,
    DocumentInfo,
    DocumentListResponse,
)

__all__ = ["build_tree_response", "list_documents_for_store"]


def _logical_entries_for_directory(
    dir_path: Path,
    root_path: Path,
    chunk_counts: dict[str, int],
) -> list[DocumentInfo]:
    """Group sibling files into logical stem entries for one directory."""
    grouped: dict[str, list[Path]] = {}
    for item in sorted(dir_path.iterdir()):
        if not item.is_file():
            continue
        relative = item.relative_to(root_path)
        stem_path = str((relative.parent / item.stem).as_posix())
        grouped.setdefault(stem_path, []).append(item)

    entries: list[DocumentInfo] = []
    for stem_path, files in sorted(grouped.items()):
        description = next(
            (file for file in files if file.suffix == DOCUMENT_EXTENSION), None
        )
        original = next(
            (file for file in files if file.suffix != DOCUMENT_EXTENSION), None
        )
        primary = original or description
        if primary is None:
            continue

        entry_path = (
            str(description.relative_to(root_path).as_posix())
            if description is not None
            else str(primary.relative_to(root_path).as_posix())
        )
        modified_at = max(file.stat().st_mtime for file in files)
        size_bytes = primary.stat().st_size
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


async def list_documents_for_store(store: Casebase) -> DocumentListResponse:
    """Build a logical-entry listing for a single casebase."""
    workspace = store.workspace_dir(settings.data_dir)
    chunk_counts = await list_document_paths(store)
    documents: list[DocumentInfo] = []

    if workspace.exists():
        for dir_path in sorted(
            p for p in workspace.rglob("*") if p.is_dir() and not is_assets_dir(p.name)
        ):
            documents.extend(
                _logical_entries_for_directory(dir_path, workspace, chunk_counts)
            )
        documents.extend(
            _logical_entries_for_directory(workspace, workspace, chunk_counts)
        )

    return DocumentListResponse(documents=documents, total_count=len(documents))


def _build_directory_tree(
    dir_path: Path,
    root_path: Path,
    chunk_counts: dict[str, int],
) -> DirectoryEntry:
    """Recursively build a logical-entry directory tree."""
    relative = str(dir_path.relative_to(root_path).as_posix())
    name = dir_path.name if dir_path != root_path else ""
    entry_path = relative if relative != "." else ""
    children: list[DirectoryEntry] = []

    if dir_path.exists():
        for item in sorted(dir_path.iterdir()):
            if item.is_dir() and not is_assets_dir(item.name):
                children.append(_build_directory_tree(item, root_path, chunk_counts))

        for file_entry in _logical_entries_for_directory(
            dir_path, root_path, chunk_counts
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
    )

    total_files = 0
    total_directories = 0

    def _count(entry: DirectoryEntry) -> None:
        nonlocal total_files, total_directories
        if entry.type == "file":
            total_files += 1
        elif entry.type == "directory":
            total_directories += 1
            for child in entry.children or []:
                _count(child)

    for child in root.children or []:
        _count(child)

    return DirectoryTreeResponse(
        root=root,
        total_files=total_files,
        total_directories=total_directories,
    )
