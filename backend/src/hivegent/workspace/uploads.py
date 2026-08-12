"""Public upload, replace, and reconvert operations.

Each runs through the phased reserve → prepare → commit lifecycle
(:func:`hivegent.workspace.commit._phased_upload`): reserve only validates and
captures the source under the lock, the slow conversion runs lock-free, and the
commit supersedes any prior entry atomically — so a failed or cancelled
conversion never destroys what was already there.
"""

from pathlib import PurePosixPath

from fastapi import HTTPException

from ..chunkers.base import EntryOrigin
from ..config import settings
from ..converters.base import is_markdown_suffix
from ..db import documents as db_documents
from ..entries import entry_exists, resolve_entry_paths, stem_path_from_reference
from ..store import Casebase
from ..types import LlmConfig, PipelineSpec, ProgressReporter, UploadCompleteEvent
from .commit import _ensure_upload_slot_locked, _phased_upload
from .metadata import _merge_entry_paths, resolve_entry
from .paths import _enforce_file_size
from .prepare import _Reserved

__all__ = [
    "reconvert",
    "replace_original",
    "upload",
]


async def upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    origin: EntryOrigin = "upload",
    original_path: str | None = None,
    original_content: bytes | None = None,
    overwrite: bool = False,
    ctx: ProgressReporter | None = None,
) -> UploadCompleteEvent:
    """Upload a document to *store*, converting and chunking as needed.

    See :func:`_phased_upload` for the reserve/prepare/commit lifecycle; here
    reserve only validates the slot and captures the source, and an overwrite
    supersedes the prior entry atomically at commit.  A markdown upload may carry
    a sibling companion original, which lands in the same commit.
    """
    if not filepath:
        raise HTTPException(status_code=400, detail="Document path required")
    if (original_path is None) != (original_content is None):
        raise ValueError("original_path and original_content must be provided together")
    _enforce_file_size(content)
    if original_content is not None:
        _enforce_file_size(original_content)
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    is_markdown = is_markdown_suffix(PurePosixPath(filepath).suffix.lower())
    if not is_markdown and original_path is not None:
        raise ValueError("A companion original is supported only for markdown")
    if original_path is not None and (
        is_markdown_suffix(PurePosixPath(original_path).suffix.lower())
        or stem_path_from_reference(original_path)
        != stem_path_from_reference(filepath)
    ):
        raise ValueError("A companion original must be a non-markdown sibling")

    async def reserve() -> _Reserved:
        _ensure_upload_slot_locked(store, filepath, overwrite=overwrite)
        workspace_dir = store.workspace_dir(settings.data_dir)
        replacing = overwrite and entry_exists(workspace_dir, filepath)
        supersede = (
            (await resolve_entry(store, filepath)).original_path if replacing else None
        )

        return _Reserved(
            reference=filepath,
            content=content,
            origin=origin,
            original_path=original_path if is_markdown else filepath,
            original_content=original_content if is_markdown else content,
            preserve=replacing,
            supersede_original=supersede,
        )

    return await _phased_upload(
        store, spec, llm, stem_reference=filepath, reserve=reserve, ctx=ctx
    )


async def replace_original(
    store: Casebase,
    safe: str,
    new_content: bytes,
    *,
    new_filename: str | None,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    ctx: ProgressReporter | None = None,
) -> UploadCompleteEvent:
    """Replace the original file backing a logical entry and reconvert.

    The new original keeps the entry's stem; only the suffix may change.  See
    :func:`_phased_upload` for the lifecycle; the swap (new original written,
    stale assets cleared, old original unlinked) happens atomically at commit,
    so a failed conversion or a cancel leaves the prior entry intact.
    """
    _enforce_file_size(new_content)
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()

    async def reserve() -> _Reserved:
        metadata = await db_documents.get_entry_metadata(store, safe)
        existing_original_rel = _merge_entry_paths(
            resolve_entry_paths(store.workspace_dir(settings.data_dir), safe), metadata
        ).original_path
        if not existing_original_rel:
            raise HTTPException(
                status_code=404,
                detail=f"No original file found for '{safe}'",
            )

        existing_suffix = PurePosixPath(existing_original_rel).suffix
        new_suffix = (
            PurePosixPath(new_filename).suffix if new_filename else existing_suffix
        ) or existing_suffix
        new_original_relpath = f"{stem_path_from_reference(safe)}{new_suffix.lower()}"

        return _Reserved(
            reference=new_original_relpath,
            content=new_content,
            origin=metadata.origin if metadata else "upload",
            original_path=new_original_relpath,
            original_content=new_content,
            preserve=True,
            supersede_original=existing_original_rel,
        )

    return await _phased_upload(
        store, spec, llm, stem_reference=safe, reserve=reserve, ctx=ctx
    )


async def reconvert(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    ctx: ProgressReporter | None = None,
) -> UploadCompleteEvent:
    """Re-run conversion and chunking for an entry's existing original.

    See :func:`_phased_upload` for the lifecycle; reserve only reads the
    existing original, and the stale assets are cleared atomically at commit.
    A cancel or a failed conversion therefore leaves the entry exactly as it
    was, so the user can simply retry.
    """
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()

    async def reserve() -> _Reserved:
        metadata = await db_documents.get_entry_metadata(store, safe)
        if not metadata or not metadata.original_path:
            raise HTTPException(
                status_code=404,
                detail=f"No original file found for '{safe}'",
            )
        original_full = store.workspace_dir(settings.data_dir) / metadata.original_path
        if not original_full.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No original file found for '{safe}'",
            )

        return _Reserved(
            reference=metadata.original_path,
            content=original_full.read_bytes(),
            origin=metadata.origin,
            original_path=metadata.original_path,
            preserve=True,
        )

    return await _phased_upload(
        store, spec, llm, stem_reference=safe, reserve=reserve, ctx=ctx
    )
