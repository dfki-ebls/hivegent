"""Inventory and tree-building helpers for document stores."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ...chunks import list_chunked_documents
from ...config import settings
from ...store import Casebase
from ...types import (
    DirectoryEntry,
    DirectoryTreeResponse,
    DocumentInfo,
    DocumentListResponse,
)

__all__ = ["build_tree_response", "list_documents_for_store"]


def _collect_original_stems(originals_dir: Path) -> set[str]:
    """Collect relative stems for all original files in a store."""
    original_stems: set[str] = set()
    if originals_dir.exists():
        for original_file in originals_dir.rglob("*"):
            if original_file.is_file():
                relative = original_file.relative_to(originals_dir)
                original_stems.add(str((relative.parent / relative.stem).as_posix()))
    return original_stems


def list_documents_for_store(store: Casebase) -> DocumentListResponse:
    """Build a document listing for a single casebase."""
    workspace = store.workspace_dir(settings.data_dir)
    original_stems = _collect_original_stems(store.originals_dir(settings.data_dir))
    documents: list[DocumentInfo] = []
    chunk_counts = list_chunked_documents(store)

    if workspace.exists():
        for file_path in sorted(workspace.rglob("*")):
            if not file_path.is_file():
                continue

            relative_path = str(file_path.relative_to(workspace).as_posix())
            relative = file_path.relative_to(workspace)
            document_stem = str((relative.parent / relative.stem).as_posix())
            stat = file_path.stat()
            kind: Literal["document", "asset"] = "document"
            for part in relative.parts[:-1]:
                if part.endswith("_assets"):
                    kind = "asset"
                    break

            documents.append(
                DocumentInfo(
                    filename=relative_path,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    chunk_count=chunk_counts.get(relative_path),
                    has_original=document_stem in original_stems,
                    kind=kind,
                )
            )

    return DocumentListResponse(documents=documents, total_count=len(documents))


def _build_directory_tree(
    dir_path: Path,
    root_path: Path,
    chunk_counts: dict[str, int],
    original_stems: set[str],
) -> DirectoryEntry:
    """Recursively build a directory tree entry."""
    relative = str(dir_path.relative_to(root_path).as_posix())
    name = dir_path.name if dir_path != root_path else ""
    entry_path = relative if relative != "." else ""
    children: list[DirectoryEntry] = []

    if dir_path.exists():
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                children.append(
                    _build_directory_tree(item, root_path, chunk_counts, original_stems)
                )
            elif item.is_file():
                file_relative = str(item.relative_to(root_path).as_posix())
                item_relative = item.relative_to(root_path)
                document_stem = str(
                    (item_relative.parent / item_relative.stem).as_posix()
                )
                stat = item.stat()
                children.append(
                    DirectoryEntry(
                        type="file",
                        name=item.name,
                        path=file_relative,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                        chunk_count=chunk_counts.get(file_relative),
                        has_original=document_stem in original_stems,
                    )
                )

    return DirectoryEntry(
        type="directory",
        name=name,
        path=entry_path,
        children=children,
    )


def build_tree_response(store: Casebase) -> DirectoryTreeResponse:
    """Build a directory tree response for any casebase."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    chunk_counts = list_chunked_documents(store)
    original_stems = _collect_original_stems(store.originals_dir(settings.data_dir))

    root = _build_directory_tree(
        workspace_dir,
        workspace_dir,
        chunk_counts,
        original_stems,
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
