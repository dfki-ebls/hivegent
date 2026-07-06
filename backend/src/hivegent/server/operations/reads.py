"""Read-only filesystem helpers for document routes.

These helpers do not mutate the workspace or metadata — they only inspect
the filesystem to resolve URLs to bytes.  Mutations live in
:mod:`hivegent.workspace`.
"""

import asyncio
import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from starlette.responses import FileResponse, PlainTextResponse, Response

from ...db.documents import get_document
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
    "attachment_disposition",
    "find_original",
    "get_document_response",
    "list_assets",
]

logger = logging.getLogger(__name__)


async def find_original(store: Casebase, safe: str) -> Path:
    """Find the absolute path of the original binary for a logical entry."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata = await get_document(store, safe)
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


def attachment_disposition(filename: str) -> str:
    """Return an RFC 6266 ``Content-Disposition: attachment`` header value."""
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


async def get_document_response(store: Casebase, safe: str) -> Response:
    """Return the raw content of a document or asset as an HTTP response.

    Non-text responses force ``Content-Disposition: attachment`` so a user
    who pastes the URL into a browser tab cannot execute attacker-uploaded
    SVG/HTML in the app's same-origin context.
    """
    workspace = store.workspace_dir(settings.data_dir)
    file_path = workspace / safe
    media_type = mimetypes.guess_type(file_path.name)[0]

    if not media_type or media_type.startswith("text/"):
        try:
            text = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
            return PlainTextResponse(text)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail="Path is not a file") from exc
        except UnicodeDecodeError:
            pass

    if not file_path.is_file():
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Document not found")
        raise HTTPException(status_code=400, detail="Path is not a file")

    return FileResponse(
        path=file_path,
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": attachment_disposition(file_path.name)},
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
                logger.warning(
                    "Failed to read asset description %s",
                    description_path,
                    exc_info=True,
                )
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
