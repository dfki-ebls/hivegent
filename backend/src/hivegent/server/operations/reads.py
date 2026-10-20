"""Read-only filesystem helpers for document routes.

These helpers do not mutate the workspace or metadata — they only inspect
the filesystem to resolve URLs to bytes.  Mutations live in
:mod:`hivegent.workspace`.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from stat import S_ISREG
from urllib.parse import quote

from fastapi import HTTPException
from starlette.responses import FileResponse, PlainTextResponse, Response

from ...converters.base import DOCUMENT_EXTENSION
from ...converters.formats import is_textual_media_type, media_type
from ...entries import assets_dir_for_stem, stem_path_from_reference
from ...l10n import Localized
from ...store import Casebase
from ...text import read_text_file
from ...tools.base import entry_stat
from ...types import AssetEntry, AssetListResponse
from ...workspace.paths import document_not_found

__all__ = [
    "attachment_disposition",
    "attachment_response",
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


# Stops a browser from sniffing served text or bytes into a type it would render.
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


def attachment_disposition(filename: str) -> str:
    """Return an RFC 6266 ``Content-Disposition: attachment`` header value."""
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def attachment_response(path: Path) -> FileResponse:
    """Serve *path* as a download under the media type its name declares."""
    return FileResponse(
        path=path,
        media_type=media_type(path.name) or "application/octet-stream",
        headers={"Content-Disposition": attachment_disposition(path.name), **_NOSNIFF},
    )


async def get_document_response(store: Casebase, safe: str) -> Response:
    """Return the raw content of a document or asset as an HTTP response."""
    return await get_file_response(lambda: store.locate(safe), store.scope.render(safe))


async def get_file_response(resolve: Callable[[], Path | None], shown: str) -> Response:
    """Resolve and read a file in one worker thread, as text or a downloadable attachment.

    *resolve* returns ``None`` for a path the caller may not read.  A file whose
    name declares no media type, or a textual one, is served as plain text when
    it decodes, since most source and config formats have no registered type.
    Everything else, an image included even when its format is markup, keeps
    its own type so the client can render it, and is forced to
    ``Content-Disposition: attachment`` so a user who pastes the URL into a
    browser tab cannot execute attacker-uploaded SVG/HTML in the app's
    same-origin context.
    """
    return await asyncio.to_thread(_file_response, resolve, shown)


def _file_response(resolve: Callable[[], Path | None], shown: str) -> Response:
    file_path = resolve()

    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail=document_not_found(shown).current)

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=_NOT_A_FILE.current)

    declared = media_type(file_path.name)

    # An SVG is markup, but has to reach an `<img>` under its own type.
    if declared is None or (
        not declared.startswith("image/") and is_textual_media_type(declared)
    ):
        try:
            decoded = read_text_file(file_path)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=document_not_found(shown).current
            ) from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=_NOT_A_FILE.current) from exc

        # Undecodable content falls through to the attachment response below.
        if decoded is not None:
            return PlainTextResponse(decoded.text, headers=_NOSNIFF)

    return attachment_response(file_path)


def list_assets(store: Casebase, safe: str) -> AssetListResponse:
    """List the asset files in a document's child-assets directory."""
    assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
    assets_path = store.locate(assets_dir)
    if assets_path is None or not assets_path.is_dir():
        raise HTTPException(status_code=404, detail=_NO_ASSETS.current)

    md_files: dict[Path, Path] = {}
    asset_files: list[tuple[Path, int]] = []
    for item in sorted(assets_path.rglob("*")):
        # A symlink counts as absent, since it could name a file outside the workspace.
        st = entry_stat(item)
        if st is None or not S_ISREG(st.st_mode):
            continue
        relative = item.relative_to(assets_path)
        if item.suffix == DOCUMENT_EXTENSION:
            md_files[relative.with_suffix("")] = relative
        else:
            asset_files.append((relative, st.st_size))

    entries: list[AssetEntry] = []
    for relative, size in asset_files:
        rel_path = f"{assets_dir}/{relative.as_posix()}"
        companion = md_files.get(relative.with_suffix(""))
        description = ""
        description_path: str | None = None
        if companion is not None:
            description_path = f"{assets_dir}/{companion.as_posix()}"
            try:
                decoded = read_text_file(assets_path / companion)
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
                size_bytes=size,
                media_type=media_type(relative.name),
            )
        )

    return AssetListResponse(assets=entries, assets_dir=assets_dir)
