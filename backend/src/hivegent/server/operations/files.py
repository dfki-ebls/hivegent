"""Filesystem operations for individual documents and directories."""

import mimetypes
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from starlette.responses import PlainTextResponse, Response

from ...chunkers.base import DocumentMetadata
from ...chunks import delete_metadata, get_metadata
from ...config import settings
from ...converters.base import DOCUMENT_EXTENSION
from ...entries import (
    assets_dir_for_stem,
    description_path_for_stem,
    entry_exists,
    metadata_path_for_reference,
    resolve_entry_paths,
    stem_path_from_reference,
)
from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import (
    AssetEntry,
    AssetListResponse,
    MoveDirectoryResponse,
    MoveDocumentResponse,
)
from ..common import cleanup_empty_parents

__all__ = [
    "delete_directory_internal",
    "delete_single",
    "ensure_upload_slot",
    "find_original",
    "get_document_response",
    "list_assets",
    "move_directory_internal",
    "move_document_internal",
    "update_asset_description",
]


@dataclass(slots=True, frozen=True)
class PathReplacement:
    """Prefix replacement for metadata path fields."""

    old: str
    new: str


def _replace_path_value(
    value: str | None,
    replacements: Sequence[PathReplacement],
) -> str | None:
    """Apply prefix replacements to a metadata path value."""
    if value is None:
        return None
    updated_value = value
    for replacement in replacements:
        if updated_value == replacement.old or updated_value.startswith(
            f"{replacement.old}/"
        ):
            updated_value = replacement.new + updated_value[len(replacement.old) :]
    return updated_value


def _rewrite_metadata_paths(
    meta_path: Path,
    replacements: Sequence[PathReplacement],
) -> None:
    """Rewrite path-like fields inside a metadata JSON file."""
    try:
        metadata = DocumentMetadata.model_validate_json(
            meta_path.read_text(encoding="utf-8")
        )
    except Exception:
        return

    updated_metadata = metadata.model_copy(
        update={
            "stem_path": _replace_path_value(metadata.stem_path, replacements)
            or metadata.stem_path,
            "description_path": _replace_path_value(
                metadata.description_path, replacements
            )
            or metadata.description_path,
            "original_path": _replace_path_value(metadata.original_path, replacements),
            "assets_dir": _replace_path_value(metadata.assets_dir, replacements),
            "files": [
                replaced
                for f in metadata.files
                if (replaced := _replace_path_value(f, replacements)) is not None
            ],
        }
    )
    meta_path.write_text(
        updated_metadata.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def _rewrite_metadata_tree(
    metadata_root: Path,
    replacements: Sequence[PathReplacement],
) -> None:
    """Rewrite path fields in all metadata files under a subtree."""
    if metadata_root.is_file():
        _rewrite_metadata_paths(metadata_root, replacements)
        return
    for meta_file in metadata_root.rglob("*.json"):
        _rewrite_metadata_paths(meta_file, replacements)


def find_original(store: Casebase, safe: str) -> Path:
    """Find the original binary file for a logical entry."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata = get_metadata(store, safe)
    original_path = metadata.original_path if metadata else None
    if not original_path:
        original_path = resolve_entry_paths(workspace_dir, safe).original_path
    if not original_path:
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{safe}'",
        )
    full_path = workspace_dir / original_path
    if not full_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{safe}'",
        )
    return full_path


def move_document_internal(
    store: Casebase,
    src: str,
    dst: str,
) -> MoveDocumentResponse:
    """Move a logical entry, its original, and its child-assets subtree."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)

    metadata = get_metadata(store, src)
    if not metadata:
        raise HTTPException(status_code=404, detail="Document not found")

    src_stem = metadata.stem_path or stem_path_from_reference(src)
    dst_stem = stem_path_from_reference(dst)
    if src_stem != dst_stem and entry_exists(workspace_dir, metadata_dir, dst):
        raise HTTPException(status_code=409, detail="Destination already exists")

    src_description = metadata.description_path or description_path_for_stem(src_stem)
    dst_description = description_path_for_stem(dst_stem)
    src_description_path = workspace_dir / src_description
    if not src_description_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    dst_description_path = workspace_dir / dst_description
    dst_description_path.parent.mkdir(parents=True, exist_ok=True)
    src_description_path.rename(dst_description_path)
    cleanup_empty_parents(src_description_path, workspace_dir)

    dst_original: str | None = None
    if metadata.original_path:
        src_original_path = workspace_dir / metadata.original_path
        if src_original_path.exists():
            dst_original = f"{dst_stem}{src_original_path.suffix}"
            dst_original_path = workspace_dir / dst_original
            dst_original_path.parent.mkdir(parents=True, exist_ok=True)
            src_original_path.rename(dst_original_path)
            cleanup_empty_parents(src_original_path, workspace_dir)

    dst_assets_dir: str | None = None
    src_assets_dir = metadata.assets_dir
    if src_assets_dir:
        src_assets_path = workspace_dir / src_assets_dir
        if src_assets_path.exists():
            dst_assets_dir = assets_dir_for_stem(dst_stem)
            dst_assets_path = workspace_dir / dst_assets_dir
            dst_assets_path.parent.mkdir(parents=True, exist_ok=True)
            src_assets_path.rename(dst_assets_path)
            cleanup_empty_parents(src_assets_path, workspace_dir)

            src_assets_name = PurePosixPath(src_assets_dir).name
            dst_assets_name = PurePosixPath(dst_assets_dir).name
            content = dst_description_path.read_text(encoding="utf-8")
            if src_assets_name != dst_assets_name:
                content = content.replace(f"{src_assets_name}/", f"{dst_assets_name}/")
                dst_description_path.write_text(content, encoding="utf-8")

    src_meta_path = metadata_path_for_reference(store, src_stem)
    dst_meta_path = metadata_path_for_reference(store, dst_stem)
    if src_meta_path.exists():
        dst_meta_path.parent.mkdir(parents=True, exist_ok=True)
        src_meta_path.rename(dst_meta_path)
        cleanup_empty_parents(src_meta_path, metadata_dir)

    src_meta_assets = metadata_dir / assets_dir_for_stem(src_stem)
    dst_meta_assets = metadata_dir / assets_dir_for_stem(dst_stem)
    if src_meta_assets.exists():
        dst_meta_assets.parent.mkdir(parents=True, exist_ok=True)
        src_meta_assets.rename(dst_meta_assets)
        cleanup_empty_parents(src_meta_assets, metadata_dir)

    replacements = [
        PathReplacement(old=src_stem, new=dst_stem),
        PathReplacement(old=src_description, new=dst_description),
    ]
    if metadata.original_path and dst_original:
        replacements.append(
            PathReplacement(old=metadata.original_path, new=dst_original)
        )
    if src_assets_dir and dst_assets_dir:
        replacements.append(PathReplacement(old=src_assets_dir, new=dst_assets_dir))
    if dst_meta_path.exists():
        _rewrite_metadata_paths(dst_meta_path, replacements)
    if dst_meta_assets.exists():
        _rewrite_metadata_tree(dst_meta_assets, replacements)

    mark_dirty_and_sync(store)
    return MoveDocumentResponse(
        source=src,
        destination=dst_description,
        message="Document moved successfully",
    )


def move_directory_internal(
    store: Casebase,
    src: str,
    dst: str,
) -> MoveDirectoryResponse:
    """Move or rename a workspace directory and its metadata subtree."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)

    src_dir = workspace_dir / src
    if not src_dir.exists() or not src_dir.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dst_dir = workspace_dir / dst
    if dst_dir.exists():
        raise HTTPException(status_code=409, detail="Destination already exists")

    files_moved = sum(1 for file_path in src_dir.rglob("*") if file_path.is_file())
    dst_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_dir), str(dst_dir))
    cleanup_empty_parents(src_dir, workspace_dir)

    src_meta = metadata_dir / src
    dst_meta = metadata_dir / dst
    if src_meta.exists():
        dst_meta.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_meta), str(dst_meta))
        cleanup_empty_parents(src_meta, metadata_dir)
        _rewrite_metadata_tree(
            dst_meta,
            [PathReplacement(old=src, new=dst)],
        )

    mark_dirty_and_sync(store)
    return MoveDirectoryResponse(
        source=src,
        destination=dst,
        files_moved=files_moved,
        message="Directory moved successfully",
    )


def get_document_response(store: Casebase, safe: str) -> Response:
    """Return the content of a document or asset from a casebase."""
    workspace = store.workspace_dir(settings.data_dir)
    file_path = workspace / safe

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    media_type = mimetypes.guess_type(file_path.name)[0]
    if media_type and not media_type.startswith("text/"):
        return Response(content=file_path.read_bytes(), media_type=media_type)

    try:
        return PlainTextResponse(file_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return Response(
            content=file_path.read_bytes(),
            media_type=media_type or "application/octet-stream",
        )


def _require_assets_dir(store: Casebase, safe: str) -> tuple[Path, str]:
    """Resolve and validate the assets directory for a document.

    Args:
        store: The casebase.
        safe: Workspace-relative document filepath.

    Returns:
        A tuple of (workspace root, assets directory relative path).

    Raises:
        HTTPException: 404 when no assets directory exists.
    """
    workspace = store.workspace_dir(settings.data_dir)
    assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
    assets_path = workspace / assets_dir
    if not assets_path.exists() or not assets_path.is_dir():
        raise HTTPException(status_code=404, detail="Document has no assets directory")
    return workspace, assets_dir


def list_assets(store: Casebase, safe: str) -> AssetListResponse:
    """List files in a document's child-assets directory.

    Groups files by stem: non-``.md`` files are returned as asset entries,
    with the content of their companion ``.md`` file (if present) as the
    description.

    Args:
        store: The casebase.
        safe: Workspace-relative document filepath.

    Returns:
        An :class:`AssetListResponse` listing each asset file.
    """
    workspace, assets_dir = _require_assets_dir(store, safe)
    assets_path = workspace / assets_dir

    md_files: dict[str, Path] = {}
    asset_files: list[Path] = []
    for item in sorted(assets_path.iterdir()):
        if not item.is_file():
            continue
        if item.suffix == DOCUMENT_EXTENSION:
            md_files[item.stem] = item
        else:
            asset_files.append(item)

    entries: list[AssetEntry] = []
    for item in asset_files:
        rel_path = str(item.relative_to(workspace).as_posix())
        companion = md_files.get(item.stem)
        description = ""
        description_path: str | None = None
        if companion is not None:
            description_path = str(companion.relative_to(workspace).as_posix())
            try:
                description = companion.read_text(encoding="utf-8")
            except Exception:
                description = ""
        entries.append(
            AssetEntry(
                name=item.name,
                path=rel_path,
                description_path=description_path,
                description=description,
                size_bytes=item.stat().st_size,
                media_type=mimetypes.guess_type(item.name)[0],
            )
        )

    return AssetListResponse(assets=entries, assets_dir=assets_dir)


def update_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    content: str,
) -> AssetEntry:
    """Update the companion ``.md`` description for an asset file.

    Creates the ``.md`` file if it does not exist yet.

    Args:
        store: The casebase.
        safe: Workspace-relative document filepath.
        asset_name: Filename of the asset (e.g. ``image_001.png``).
        content: New text content for the companion ``.md`` file.

    Returns:
        The updated :class:`AssetEntry`.
    """
    workspace, assets_dir = _require_assets_dir(store, safe)

    asset_path = workspace / assets_dir / asset_name
    if not asset_path.exists() or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Asset file not found")

    md_path = asset_path.with_suffix(DOCUMENT_EXTENSION)
    md_path.write_text(content, encoding="utf-8")

    rel_path = str(asset_path.relative_to(workspace).as_posix())
    description_path = str(md_path.relative_to(workspace).as_posix())

    mark_dirty_and_sync(store)

    return AssetEntry(
        name=asset_name,
        path=rel_path,
        description_path=description_path,
        description=content,
        size_bytes=asset_path.stat().st_size,
        media_type=mimetypes.guess_type(asset_name)[0],
    )


def ensure_upload_slot(store: Casebase, reference: str, *, overwrite: bool) -> None:
    """Check that an upload slot is free, deleting the existing entry on overwrite.

    Args:
        store: The casebase.
        reference: Workspace-relative file reference.
        overwrite: If ``True``, delete the existing entry instead of raising.

    Raises:
        HTTPException: 409 when the entry exists and *overwrite* is ``False``.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    if not entry_exists(workspace_dir, metadata_dir, reference):
        return
    if not overwrite:
        raise HTTPException(status_code=409, detail="Document already exists")
    delete_single(store, description_path_for_stem(stem_path_from_reference(reference)))


def delete_single(store: Casebase, safe: str) -> None:
    """Delete a logical entry, its original, and its child-assets subtree."""
    workspace = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    metadata = get_metadata(store, safe)
    if not metadata:
        description_path = workspace / safe
        if not description_path.exists():
            raise HTTPException(status_code=404, detail="Document not found")
        if not description_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
    resolved = resolve_entry_paths(workspace, safe)
    description_rel = (
        metadata.description_path
        if metadata and metadata.description_path
        else resolved.description_path
    )
    description_path = workspace / description_rel
    if description_path.exists():
        description_path.unlink()
        cleanup_empty_parents(description_path, workspace)

    original_rel = metadata.original_path if metadata else resolved.original_path
    if original_rel:
        original_path = workspace / original_rel
        if original_path.exists():
            original_path.unlink()
            cleanup_empty_parents(original_path, workspace)

    assets_rel = metadata.assets_dir if metadata else resolved.assets_dir
    if assets_rel:
        assets_path = workspace / assets_rel
        if assets_path.exists():
            shutil.rmtree(assets_path)
            cleanup_empty_parents(assets_path, workspace)
        metadata_assets = metadata_dir / assets_rel
        if metadata_assets.exists():
            shutil.rmtree(metadata_assets)
            cleanup_empty_parents(metadata_assets, metadata_dir)

    delete_metadata(store, safe)


def delete_directory_internal(store: Casebase, safe: str) -> int:
    """Delete a directory tree from workspace and metadata."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)

    directory_path = workspace_dir / safe
    if not directory_path.exists() or not directory_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    files_deleted = sum(
        1 for file_path in directory_path.rglob("*") if file_path.is_file()
    )

    shutil.rmtree(directory_path)
    cleanup_empty_parents(directory_path, workspace_dir)

    metadata_subdir = metadata_dir / safe
    if metadata_subdir.exists():
        shutil.rmtree(metadata_subdir)
        cleanup_empty_parents(metadata_subdir, metadata_dir)

    return files_deleted
