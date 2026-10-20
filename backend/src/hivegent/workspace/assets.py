"""Companion-description operations for a document's stored assets.

Update, generate, or clear the ``.md`` description that sits beside an asset
file inside a document's ``.assets`` directory.  Each is one changeset the
host commits through the gateway, the only one that may change an ``.assets``
payload, which takes the description for a unit of its own that needs its
asset.  So a description is size-capped, indexed, and announced as every
other change, and refused once its asset is gone.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from ..changes import Changeset, Delete, Write
from ..config import sanitize_document_path, settings
from ..converters.asset_processing import image_context_windows
from ..converters.base import DOCUMENT_EXTENSION
from ..converters.formats import media_type
from ..converters.images import guess_image_media_type
from ..entries import (
    asset_ref_for,
    assets_dir_for_stem,
    description_path_for_stem,
    stem_path_from_reference,
)
from ..l10n import Localized
from ..llm_config import LlmConfig
from ..store import Casebase
from ..text import read_text_file
from ..types import AssetEntry
from .changeset import apply_changeset, plan_changeset
from .describe import _build_image_description
from .operations import Location

__all__ = [
    "delete_asset_description",
    "generate_asset_description",
    "update_asset_description",
]

_ASSET_ESCAPES = Localized(
    en="Asset path escapes assets directory",
    de="Der Asset-Pfad verlässt den Asset-Ordner",
)
_ASSET_NOT_FOUND = Localized(en="Asset file not found", de="Asset-Datei nicht gefunden")


@dataclass(slots=True, frozen=True)
class _Asset:
    """An asset file in a document's ``.assets`` directory.

    Attributes:
        workspace: The workspace holding it.
        name: Its path within the ``.assets`` directory.
        path: Its path on disk.
    """

    workspace: Path
    name: str
    path: Path

    @property
    def description_path(self) -> str:
        """The workspace-relative path of its companion ``.md``."""
        return (
            self.path.with_suffix(DOCUMENT_EXTENSION)
            .relative_to(self.workspace)
            .as_posix()
        )

    @property
    def size(self) -> int:
        """Its size in bytes."""
        return self.path.stat().st_size

    def entry(self, description: str, description_path: str | None) -> AssetEntry:
        """The asset as a listing shows it, with *description* at *description_path*."""
        return AssetEntry(
            name=self.name,
            path=self.path.relative_to(self.workspace).as_posix(),
            description_path=description_path,
            description=description,
            size_bytes=self.size,
            media_type=media_type(self.name),
        )


def _resolve_asset(store: Casebase, safe: str, asset_name: str) -> _Asset:
    """Resolve an existing asset of *safe*'s entry.

    Rejects a path that escapes the entry's ``.assets`` directory, and 404s
    when the asset itself is absent.
    """
    workspace = store.workspace_dir(settings.data_dir)
    assets_path = workspace / assets_dir_for_stem(stem_path_from_reference(safe))

    try:
        name = sanitize_document_path(asset_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    path = assets_path / name

    if not path.resolve().is_relative_to(assets_path.resolve()):
        raise HTTPException(status_code=400, detail=_ASSET_ESCAPES.current)

    if not path.is_file():
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND.current)

    return _Asset(workspace, name, path)


async def _commit_as_host(
    item: Write[Location] | Delete[Location],
    owner: str | None,
    exclude_client: str | None,
) -> None:
    await apply_changeset(
        (item,), owner=owner, exclude_client=exclude_client, host=True
    )


async def update_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    content: str,
    *,
    owner: str | None = None,
    exclude_client: str | None = None,
) -> AssetEntry:
    """Update the companion ``.md`` description of an asset.

    The description is indexed by the time the call returns, and *owner*'s
    clients but *exclude_client* hear about it.
    """
    asset = _resolve_asset(store, safe, asset_name)
    write = Write(Location(store, asset.description_path), content)
    await _commit_as_host(write, owner, exclude_client)

    return asset.entry(content, asset.description_path)


def _vision_input(asset: _Asset, safe: str) -> tuple[bytes, list[str]]:
    """The asset's bytes and what grounds its description: its name and where the document shows it."""
    contexts = [f"File name: {asset.name}"]
    stem_path = stem_path_from_reference(safe)
    parent_md = asset.workspace / description_path_for_stem(stem_path)
    parent = read_text_file(parent_md) if parent_md.is_file() else None

    if parent is not None:
        windows = image_context_windows(parent.text)
        ref = asset_ref_for(assets_dir_for_stem(stem_path), asset.name)
        contexts.extend(windows.get(ref, []))

    return asset.path.read_bytes(), contexts


async def generate_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    llm: LlmConfig,
    *,
    owner: str | None = None,
    exclude_client: str | None = None,
) -> AssetEntry:
    """Describe an asset with the vision model and write the result.

    Mirrors :func:`update_asset_description` but derives the companion
    ``.md`` content from the asset bytes via the same describe pipeline used
    during ingestion.  The write is planned first, so the model never
    describes an asset the gateway refuses, and runs before the write takes
    the lock, which refuses the description should the asset vanish meanwhile.
    """
    asset = _resolve_asset(store, safe, asset_name)
    location = Location(store, asset.description_path)
    await plan_changeset(Changeset((Write(location, ""),)), host=True)
    content, contexts = await asyncio.to_thread(_vision_input, asset, safe)
    media_type = guess_image_media_type(asset.name) or ""
    description = await _build_image_description(
        asset.name, content, media_type, contexts, llm
    )
    await _commit_as_host(Write(location, description), owner, exclude_client)

    return asset.entry(description, asset.description_path)


async def delete_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    *,
    owner: str | None = None,
    exclude_client: str | None = None,
) -> AssetEntry:
    """Delete the companion ``.md`` description of an asset.

    The inverse of :func:`update_asset_description`: it removes the
    description file and its rows while leaving the asset itself in place,
    and returns the asset entry with its description cleared.  An asset
    that was never described is left as it is.
    """
    asset = _resolve_asset(store, safe, asset_name)
    await _commit_as_host(
        Delete(Location(store, asset.description_path)), owner, exclude_client
    )

    return asset.entry("", None)
