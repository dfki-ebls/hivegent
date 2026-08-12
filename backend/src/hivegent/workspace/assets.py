"""Companion-description operations for a document's stored assets.

Update, generate, or clear the ``.md`` description that sits beside an asset
file inside a document's ``.assets`` directory.  Each holds the casebase lock
and keeps the description file and its chunk rows in agreement.
"""

import mimetypes
from pathlib import Path

from fastapi import HTTPException

from ..concurrency import shield_to_completion
from ..config import sanitize_document_path, settings
from ..converters.asset_processing import image_context_windows
from ..converters.base import DOCUMENT_EXTENSION
from ..converters.images import guess_image_media_type
from ..entries import (
    asset_ref_for,
    assets_dir_for_stem,
    description_path_for_stem,
    stem_path_from_reference,
)
from ..store import Casebase
from ..text import read_text_file
from ..types import AssetEntry, LlmConfig
from .describe import _build_image_description
from .indexing import chunk_and_index_document, delete_chunked_document
from .locks import _locked_for
from .paths import _write_markdown_file

__all__ = [
    "delete_asset_description",
    "generate_asset_description",
    "update_asset_description",
]


def _resolve_existing_asset(
    store: Casebase, safe: str, asset_name: str
) -> tuple[Path, str, Path]:
    """Resolve the workspace root and validated path of an existing asset file.

    Returns ``(workspace, relative_path, asset_path)``, rejecting a path that
    escapes the owning document's ``.assets`` directory and 404-ing when the
    asset itself is absent.  The caller holds the store lock, so the file cannot
    vanish between this check and its use.
    """
    workspace = store.workspace_dir(settings.data_dir)
    assets_path = workspace / assets_dir_for_stem(stem_path_from_reference(safe))

    try:
        safe_name = sanitize_document_path(asset_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    asset_path = assets_path / safe_name
    if not asset_path.resolve().is_relative_to(assets_path.resolve()):
        raise HTTPException(
            status_code=400,
            detail="Asset path escapes assets directory",
        )
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Asset file not found")

    return workspace, safe_name, asset_path


def _asset_entry(
    workspace: Path,
    asset_path: Path,
    relative_path: str,
    size_bytes: int,
    description: str,
    description_path: str | None,
) -> AssetEntry:
    """Build an :class:`AssetEntry` from a resolved asset path."""
    return AssetEntry(
        name=relative_path,
        path=asset_path.relative_to(workspace).as_posix(),
        description_path=description_path,
        description=description,
        size_bytes=size_bytes,
        media_type=mimetypes.guess_type(relative_path)[0],
    )


async def _persist_asset_description(
    store: Casebase,
    workspace: Path,
    asset_path: Path,
    relative_path: str,
    content: str,
    size_bytes: int,
) -> AssetEntry:
    """Write a companion ``.md`` file, index it, and return the asset entry.

    Callers pass the asset's *size_bytes* since they already hold either a
    ``stat`` (update) or the raw bytes (generate), avoiding a redundant
    filesystem round-trip here.
    """
    md_path = asset_path.with_suffix(DOCUMENT_EXTENSION)
    description_path = md_path.relative_to(workspace).as_posix()
    stat = _write_markdown_file(workspace, description_path, content)
    await chunk_and_index_document(store, description_path, content, stat=stat)
    return _asset_entry(
        workspace, asset_path, relative_path, size_bytes, content, description_path
    )


async def _clear_asset_description_locked(
    store: Casebase, md_path: Path, description_path: str
) -> None:
    """Remove a companion ``.md`` description file and its chunk rows.

    Caller holds the store lock. Tolerates a missing file and absent rows so
    clearing an asset that was only stored (never described) is a no-op.
    """
    md_path.unlink(missing_ok=True)
    await delete_chunked_document(store, description_path)


async def update_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    content: str,
) -> AssetEntry:
    """Update the companion ``.md`` description of an asset.

    Persists chunk metadata for the description so it is searchable
    immediately after the call returns.
    """
    async with _locked_for(store, safe):
        workspace, safe_name, asset_path = _resolve_existing_asset(
            store, safe, asset_name
        )
        return await shield_to_completion(
            _persist_asset_description(
                store,
                workspace,
                asset_path,
                safe_name,
                content,
                asset_path.stat().st_size,
            )
        )


async def generate_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    llm: LlmConfig,
) -> AssetEntry:
    """Describe an asset with the vision model and persist the result.

    Mirrors :func:`update_asset_description` but derives the companion
    ``.md`` content from the asset bytes via the same describe pipeline used
    during ingestion, instead of receiving it from the caller.
    """
    async with _locked_for(store, safe):
        workspace, safe_name, asset_path = _resolve_existing_asset(
            store, safe, asset_name
        )
        content_bytes = asset_path.read_bytes()

        media_type = guess_image_media_type(safe_name) or ""
        contexts = [f"File name: {safe_name}"]
        stem_path = stem_path_from_reference(safe)
        parent_md = workspace / description_path_for_stem(stem_path)
        parent = read_text_file(parent_md) if parent_md.is_file() else None
        if parent is not None:
            windows = image_context_windows(parent.text)
            ref = asset_ref_for(assets_dir_for_stem(stem_path), safe_name)
            contexts.extend(windows.get(ref, []))
        description = await _build_image_description(
            safe_name, content_bytes, media_type, contexts, llm
        )
        return await shield_to_completion(
            _persist_asset_description(
                store,
                workspace,
                asset_path,
                safe_name,
                description,
                len(content_bytes),
            )
        )


async def delete_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
) -> AssetEntry:
    """Delete the companion ``.md`` description of an asset.

    The inverse of :func:`update_asset_description`: it removes the
    description file and its chunk rows while leaving the asset itself in
    place, and returns the asset entry with its description cleared. The file
    and SQL removal runs to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so it cannot strand the file without its
    rows or vice versa.
    """
    async with _locked_for(store, safe):
        workspace, safe_name, asset_path = _resolve_existing_asset(
            store, safe, asset_name
        )
        size_bytes = asset_path.stat().st_size
        md_path = asset_path.with_suffix(DOCUMENT_EXTENSION)
        description_path = md_path.relative_to(workspace).as_posix()
        await shield_to_completion(
            _clear_asset_description_locked(store, md_path, description_path)
        )
        return _asset_entry(workspace, asset_path, safe_name, size_bytes, "", None)
