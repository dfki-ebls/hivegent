"""Filesystem operations for individual documents and directories."""

import mimetypes
import shutil
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import PlainTextResponse, Response

from ...chunks import delete_metadata, get_metadata, load_document_metadata
from ...config import settings
from ...converters.base import DOCUMENT_EXTENSION
from ...retrieval import mark_dirty
from ...store import Casebase
from ...types import MoveDocumentResponse
from ..common import cleanup_empty_parents

__all__ = [
    "delete_directory_internal",
    "delete_single",
    "find_original",
    "get_document_response",
    "move_document_internal",
]


def move_document_internal(
    store: Casebase,
    src: str,
    dst: str,
) -> MoveDocumentResponse:
    """Move a document, its metadata, companion images, and original."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    originals_dir = store.originals_dir(settings.data_dir)

    src_doc = workspace_dir / src
    if not src_doc.exists() or not src_doc.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    dst_doc = workspace_dir / dst
    if dst_doc.exists():
        raise HTTPException(status_code=409, detail="Destination already exists")

    dst_doc.parent.mkdir(parents=True, exist_ok=True)
    src_doc.rename(dst_doc)
    cleanup_empty_parents(src_doc, workspace_dir)

    src_meta_stem = src.removesuffix(DOCUMENT_EXTENSION)
    src_meta = metadata_dir / f"{src_meta_stem}.json"
    dst_meta_stem = dst.removesuffix(DOCUMENT_EXTENSION)
    dst_meta = metadata_dir / f"{dst_meta_stem}.json"
    if src_meta.exists():
        dst_meta.parent.mkdir(parents=True, exist_ok=True)
        src_meta.rename(dst_meta)
        cleanup_empty_parents(src_meta, metadata_dir)

    src_path = Path(src)
    dst_path = Path(dst)
    src_base = src_path.stem
    dst_base = dst_path.stem
    src_parent_str = str(src_path.parent)
    dst_parent_str = str(dst_path.parent)

    old_assets_name = f"{src_base}_assets"
    new_assets_name = f"{dst_base}_assets"
    old_assets_dir = (
        workspace_dir / src_parent_str / old_assets_name
        if src_parent_str != "."
        else workspace_dir / old_assets_name
    )

    if old_assets_dir.is_dir():
        new_assets_dir = (
            workspace_dir / dst_parent_str / new_assets_name
            if dst_parent_str != "."
            else workspace_dir / new_assets_name
        )
        new_assets_dir.parent.mkdir(parents=True, exist_ok=True)
        old_assets_dir.rename(new_assets_dir)
        cleanup_empty_parents(old_assets_dir, workspace_dir)

        content = dst_doc.read_text(encoding="utf-8")
        content = content.replace(f"]({old_assets_name}/", f"]({new_assets_name}/")
        dst_doc.write_text(content, encoding="utf-8")

        if dst_meta.exists():
            meta = load_document_metadata(metadata_dir, dst)
            if meta and meta.images:
                updated_images = [
                    image.replace(f"{old_assets_name}/", f"{new_assets_name}/")
                    for image in meta.images
                ]
                if src_parent_str != dst_parent_str:
                    old_prefix = (
                        f"{src_parent_str}/{old_assets_name}/"
                        if src_parent_str != "."
                        else f"{old_assets_name}/"
                    )
                    new_prefix = (
                        f"{dst_parent_str}/{new_assets_name}/"
                        if dst_parent_str != "."
                        else f"{new_assets_name}/"
                    )
                    updated_images = [
                        image.replace(old_prefix, new_prefix)
                        for image in updated_images
                    ]
                updated = meta.model_copy(update={"images": updated_images})
                dst_meta.write_text(
                    updated.model_dump_json(indent=2),
                    encoding="utf-8",
                )

    src_stem = src_path.stem
    search_dir = (
        originals_dir / src_parent_str if src_parent_str != "." else originals_dir
    )
    if search_dir.exists():
        for candidate in search_dir.iterdir():
            if candidate.is_file() and candidate.stem == src_stem:
                destination_dir = (
                    originals_dir / dst_parent_str
                    if dst_parent_str != "."
                    else originals_dir
                )
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = destination_dir / (dst_path.stem + candidate.suffix)
                candidate.rename(destination)
                cleanup_empty_parents(candidate, originals_dir)
                break

    mark_dirty(store)
    return MoveDocumentResponse(
        source=src,
        destination=dst,
        message="Document moved successfully",
    )


def find_original(store: Casebase, safe: str) -> Path:
    """Find the original binary file by stem-matching from a workspace path."""
    originals_dir = store.originals_dir(settings.data_dir)
    safe_path = Path(safe)
    target_stem = safe_path.stem
    parent = str(safe_path.parent)
    search_dir = originals_dir / parent if parent != "." else originals_dir

    if search_dir.exists():
        for candidate in search_dir.iterdir():
            if candidate.is_file() and candidate.stem == target_stem:
                return candidate

    raise HTTPException(
        status_code=404,
        detail=f"No original file found for '{safe}'",
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


def delete_single(store: Casebase, safe: str) -> None:
    """Delete a single document, its metadata, companion images, and original."""
    workspace = store.workspace_dir(settings.data_dir)
    file_path = workspace / safe

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    meta = get_metadata(store, safe)
    if meta and meta.images:
        for image_path in meta.images:
            image_full = workspace / image_path
            if image_full.exists():
                image_full.unlink()
                cleanup_empty_parents(image_full, workspace)

    file_path.unlink()
    cleanup_empty_parents(file_path, workspace)
    delete_metadata(store, safe)

    originals_dir = store.originals_dir(settings.data_dir)
    stem = Path(safe).stem
    parent = str(Path(safe).parent)
    original_dir = originals_dir / parent if parent != "." else originals_dir
    if original_dir.exists():
        for candidate in original_dir.iterdir():
            if candidate.is_file() and candidate.stem == stem:
                candidate.unlink()
                cleanup_empty_parents(candidate, originals_dir)
                break


def delete_directory_internal(store: Casebase, safe: str) -> int:
    """Delete a directory tree from workspace, metadata, and originals."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    originals_dir = store.originals_dir(settings.data_dir)

    directory_path = workspace_dir / safe
    if not directory_path.exists() or not directory_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    files_deleted = sum(
        1 for file_path in directory_path.rglob("*") if file_path.is_file()
    )

    shutil.rmtree(directory_path)
    cleanup_empty_parents(directory_path, workspace_dir)

    metadata_subdir = metadata_dir / safe
    if metadata_subdir.exists() and metadata_subdir.is_dir():
        shutil.rmtree(metadata_subdir)
        cleanup_empty_parents(metadata_subdir, metadata_dir)

    originals_subdir = originals_dir / safe
    if originals_subdir.exists() and originals_subdir.is_dir():
        shutil.rmtree(originals_subdir)
        cleanup_empty_parents(originals_subdir, originals_dir)

    return files_deleted
