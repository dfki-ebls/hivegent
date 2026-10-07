"""Read-only filesystem helpers for document routes.

These helpers do not mutate the workspace or metadata — they only inspect
the filesystem to resolve URLs to bytes.  Mutations live in
:mod:`hivegent.workspace`.
"""

import asyncio
import logging
import mimetypes
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from starlette.responses import FileResponse, PlainTextResponse, Response

from ...config import settings
from ...converters.base import DOCUMENT_EXTENSION
from ...entries import assets_dir_for_stem, stem_path_from_reference
from ...l10n import Localized
from ...store import Casebase
from ...text import read_text_file
from ...types import AssetEntry, AssetListResponse
from ...workspace import resolve_entry
from ...workspace.paths import document_not_found, no_original

__all__ = [
    "attachment_disposition",
    "find_original",
    "get_document_response",
    "get_file_response",
    "list_assets",
]

logger = logging.getLogger(__name__)

_NOT_A_FILE = Localized(en="Path is not a file", de="Der Pfad ist keine Datei")
_NO_ASSETS = Localized(
    en="Document has no assets directory",
    de="Das Dokument hat keinen Asset-Ordner",
)


async def find_original(store: Casebase, safe: str) -> Path:
    """Find the absolute path of the original binary for a logical entry."""
    original_path = (await resolve_entry(store, safe)).original_path
    full_path = (
        store.workspace_dir(settings.data_dir) / original_path
        if original_path
        else None
    )
    if full_path is None or not full_path.exists():
        raise HTTPException(
            status_code=404,
            detail=no_original(store.scope.render(safe)).current,
        )
    return full_path


def attachment_disposition(filename: str) -> str:
    """Return an RFC 6266 ``Content-Disposition: attachment`` header value."""
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


async def get_document_response(store: Casebase, safe: str) -> Response:
    """Return the raw content of a document or asset as an HTTP response."""
    return await get_file_response(
        lambda: store.workspace_dir(settings.data_dir) / safe, store.scope.render(safe)
    )


async def get_file_response(resolve: Callable[[], Path | None], shown: str) -> Response:
    """Resolve and read a file in one worker thread, as text or a downloadable attachment.

    *resolve* returns ``None`` for a path the caller may not read.  Non-text
    responses force ``Content-Disposition: attachment`` so a user who pastes
    the URL into a browser tab cannot execute attacker-uploaded SVG/HTML in
    the app's same-origin context.
    """
    return await asyncio.to_thread(_file_response, resolve, shown)


def _file_response(resolve: Callable[[], Path | None], shown: str) -> Response:
    file_path = resolve()

    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail=document_not_found(shown).current)

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=_NOT_A_FILE.current)

    media_type = mimetypes.guess_type(file_path.name)[0]

    if not media_type or media_type.startswith("text/"):
        try:
            decoded = read_text_file(file_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=document_not_found(shown).current) from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=_NOT_A_FILE.current) from exc

        # Undecodable content falls through to the attachment response below.
        if decoded is not None:
            return PlainTextResponse(decoded.text)

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
        raise HTTPException(status_code=404, detail=_NO_ASSETS.current)

    md_files: dict[Path, Path] = {}
    asset_files: list[Path] = []
    for item in sorted(assets_path.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(assets_path)
        if item.suffix == DOCUMENT_EXTENSION:
            md_files[relative.with_suffix("")] = item
        else:
            asset_files.append(item)

    entries: list[AssetEntry] = []
    for item in asset_files:
        relative = item.relative_to(assets_path)
        rel_path = item.relative_to(workspace).as_posix()
        companion = md_files.get(relative.with_suffix(""))
        description = ""
        description_path: str | None = None
        if companion is not None:
            description_path = companion.relative_to(workspace).as_posix()
            try:
                decoded = read_text_file(companion)
                description = decoded.text if decoded is not None else ""
            except OSError:
                logger.warning(
                    "Failed to read asset description %s",
                    description_path,
                    exc_info=True,
                )
        entries.append(
            AssetEntry(
                name=relative.as_posix(),
                path=rel_path,
                description_path=description_path,
                description=description,
                size_bytes=item.stat().st_size,
                media_type=mimetypes.guess_type(item.name)[0],
            )
        )

    return AssetListResponse(assets=entries, assets_dir=assets_dir)
