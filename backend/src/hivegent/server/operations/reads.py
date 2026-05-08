"""Read-only filesystem helpers for document routes.

These helpers do not mutate the workspace or metadata — they only inspect
the filesystem to resolve URLs to bytes.  Mutations live in
:mod:`hivegent.workspace`.
"""

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import PlainTextResponse, Response

from ...chunks import get_metadata
from ...config import settings
from ...converters.base import DOCUMENT_EXTENSION
from ...entries import (
    assets_dir_for_stem,
    resolve_entry_paths,
    stem_path_from_reference,
)
from ...store import Casebase
from ...types import AssetEntry, AssetListResponse

__all__ = [
    "find_original",
    "get_document_response",
    "list_assets",
]


def find_original(store: Casebase, safe: str) -> Path:
    """Find the absolute path of the original binary for a logical entry."""
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


def get_document_response(store: Casebase, safe: str) -> Response:
    """Return the raw content of a document or asset as an HTTP response."""
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


def list_assets(store: Casebase, safe: str) -> AssetListResponse:
    """List the asset files in a document's child-assets directory."""
    workspace = store.workspace_dir(settings.data_dir)
    assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
    assets_path = workspace / assets_dir
    if not assets_path.exists() or not assets_path.is_dir():
        raise HTTPException(status_code=404, detail="Document has no assets directory")

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
