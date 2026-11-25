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

from exar.ingest import contexts as occurrences
from exar.text import decode

from ..config import sanitize_document_path
from ..media import guess_image_media_type
from ..shared.formats import DOCUMENT_EXTENSION, media_type
from ..shared.l10n import Localized
from ..store import Workspace
from ..tools.changes import ChangeRefused, Changeset, Delete, Write
from ..tools.entries import Part, asset_ref_for, stem_path_from_reference
from ..types import AssetEntry
from .changeset import apply_changeset, plan_changeset
from .describe import _build_image_description
from .operations import Location, Root

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


def _resolve_asset(store: Workspace, safe: str, asset_name: str) -> _Asset:
    """Resolve an existing asset of *safe*'s entry.

    Rejects a path that escapes the entry's ``.assets`` directory, and 404s
    when the asset itself is absent.
    """
    workspace = store.workspace_dir()
    assets_path = workspace / Part(stem_path_from_reference(safe), "assets").path

    try:
        name = sanitize_document_path(asset_name)
    except ValueError as exc:
        raise ChangeRefused(status_code=400, detail=str(exc)) from exc

    path = assets_path / name

    if not path.resolve().is_relative_to(assets_path.resolve()):
        raise ChangeRefused(status_code=400, detail=_ASSET_ESCAPES.current)

    if not path.is_file():
        raise ChangeRefused(status_code=404, detail=_ASSET_NOT_FOUND.current)

    return _Asset(workspace, name, path)


async def _commit_as_host(item: Write[Location] | Delete[Location]) -> None:
    await apply_changeset((item,), host=True)


async def update_asset_description(
    store: Workspace,
    safe: str,
    asset_name: str,
    content: str,
) -> AssetEntry:
    """Update the companion ``.md`` description of an asset.

    The description is indexed by the time the call returns.
    """
    asset = _resolve_asset(store, safe, asset_name)
    await _commit_as_host(
        Write(Location(Root.of(store), asset.description_path), content)
    )

    return asset.entry(content, asset.description_path)


def _vision_input(asset: _Asset, safe: str) -> tuple[bytes, list[str]]:
    """The asset's bytes and what grounds its description: its name and where the document shows it."""
    contexts = [f"File name: {asset.name}"]
    stem_path = stem_path_from_reference(safe)
    parent_md = asset.workspace / Part(stem_path, "description").path
    parent = decode(parent_md.read_bytes()) if parent_md.is_file() else None

    if parent is not None:
        windows = occurrences(parent.text)
        ref = asset_ref_for(Part(stem_path, "assets").path, asset.name)
        contexts.extend(windows.get(ref, []))

    return asset.path.read_bytes(), contexts


async def generate_asset_description(
    store: Workspace,
    safe: str,
    asset_name: str,
) -> AssetEntry:
    """Describe an asset with the vision model and write the result.

    Mirrors :func:`update_asset_description` but derives the companion
    ``.md`` content from the asset bytes via the same describe pipeline used
    during ingestion.  The write is planned first, so the model never
    describes an asset the gateway refuses, and runs before the write takes
    the lock, which refuses the description should the asset vanish meanwhile.
    """
    asset = _resolve_asset(store, safe, asset_name)
    location = Location(Root.of(store), asset.description_path)
    await plan_changeset(Changeset((Write(location, ""),)), host=True)
    content, contexts = await asyncio.to_thread(_vision_input, asset, safe)
    media_type = guess_image_media_type(asset.name) or ""
    description = await _build_image_description(
        asset.name, content, media_type, contexts
    )
    await _commit_as_host(Write(location, description))

    return asset.entry(description, asset.description_path)


async def delete_asset_description(
    store: Workspace,
    safe: str,
    asset_name: str,
) -> AssetEntry:
    """Delete the companion ``.md`` description of an asset.

    The inverse of :func:`update_asset_description`: it removes the
    description file and its rows while leaving the asset itself in place,
    and returns the asset entry with its description cleared.  An asset
    that was never described is left as it is.
    """
    asset = _resolve_asset(store, safe, asset_name)
    await _commit_as_host(Delete(Location(Root.of(store), asset.description_path)))

    return asset.entry("", None)
