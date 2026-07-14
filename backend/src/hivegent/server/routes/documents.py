"""Routes for document and collection management.

Every workspace reference lives in the URL path as a canonical
workspace path: ``~/<local>`` for the caller's personal store, or
``@<group>/<local>`` for a group the caller can access. Scope-level
endpoints (collection upload, delete-all) take the bare scope
segment (``~`` or ``@<group>``); item endpoints take the full path.
:func:`resolve_workspace_path` maps either to its store and enforces
group membership (reads) or write access.
"""

import asyncio
import logging
import mimetypes
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from starlette.responses import FileResponse, Response

from ... import workspace
from ...auth import User, get_current_user
from ...chunkers.base import DocumentMetadata
from ...concurrency import shield_to_completion
from ...config import settings
from ...db.documents import get_document, get_line_counts
from ...store import Casebase
from ..jobs import JobContext, JobView, JobWork, manager
from ...types import (
    AssetEntry,
    AssetListResponse,
    BulkDeleteDocumentsResponse,
    CollectionCompleteEvent,
    CollectionProgressEvent,
    DeleteDocumentResponse,
    DocumentLineCountsResponse,
    GenerateAssetDescriptionRequest,
    LlmConfig,
    MoveDocumentRequest,
    MoveDocumentResponse,
    PipelineSpec,
    UpdateAssetDescriptionRequest,
    WriteDocumentRequest,
    WriteDocumentResponse,
)
from ..common import (
    parse_pipeline_spec,
    prepare_llm_config,
    resolve_move,
    resolve_workspace_path,
)
from ..models import (
    BulkDeleteRequest,
    BulkMoveRequest,
    BulkRechunkRequest,
    BulkReconvertRequest,
    DocumentLineCountsRequest,
    ReconvertRequest,
)
from ..operations import (
    attachment_disposition,
    enforce_upload_size,
    find_original,
    get_document_response,
    list_assets,
    run_bulk_document_job,
    spool_dir,
    summarize_failed_files,
    validate_collection_upload,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()

# Read buffer for streaming an upload to its spool file.
_SPOOL_CHUNK_SIZE = 1024 * 1024


async def _spool_payload(file: UploadFile, *, limit: int, label: str) -> Path:
    """Persist an upload to a temp file so a queued job needn't pin it in RAM.

    Starlette has already spooled the upload to ``file.file`` (rolling it to
    disk past its in-memory threshold); this copies it to a file we own so it
    survives past the request, which closes Starlette's temp file.  The job
    reads it back when it runs and its ``on_settled`` finalizer unlinks it, so a
    job waiting behind the concurrency limit holds only a path — not the whole
    payload — which bounds memory when many uploads are enqueued.  Spool files
    live in a managed directory swept at startup (:func:`cleanup_spool_dir`), so
    a restart that cuts a job short cannot leak them.
    """
    enforce_upload_size(file, limit=limit, label=label)

    def _write() -> Path:
        file.file.seek(0)
        with tempfile.NamedTemporaryFile(
            prefix="upload-", dir=spool_dir(), delete=False
        ) as tmp:
            try:
                shutil.copyfileobj(file.file, tmp, _SPOOL_CHUNK_SIZE)
            except BaseException:
                Path(tmp.name).unlink(missing_ok=True)
                raise

        return Path(tmp.name)

    return await asyncio.to_thread(_write)


class DocumentJobKind(StrEnum):
    """Kinds of document background jobs — the closed set the routes submit.

    Every value is prefixed ``document.``: the client keys its scope refresh on
    that prefix, so this enum is the single source of truth for the contract and
    keeps the ``kind`` strings out of the route bodies.
    """

    UPLOAD = "document.upload"
    REPLACE_ORIGINAL = "document.replace_original"
    COLLECTION = "document.collection"
    RECONVERT = "document.reconvert"
    RECHUNK = "document.rechunk"
    RECHUNK_BULK = "document.rechunk_bulk"
    RECONVERT_BULK = "document.reconvert_bulk"
    MOVE_BULK = "document.move_bulk"
    DELETE_BULK = "document.delete_bulk"


def _plural(count: int, noun: str) -> str:
    """Render ``count`` with a naively pluralised ``noun`` for a job title."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _submit_document_job(
    *,
    user: User,
    store: Casebase,
    safe: str,
    kind: DocumentJobKind,
    work: JobWork,
    on_settled: Callable[[], None] | None = None,
) -> JobView:
    """Submit a single-document job, deriving the standard title/owner/scope.

    The title is the document's filename and the scope its workspace, so the
    client attributes the job and refreshes the right view when it settles.
    """
    return manager.submit(
        kind=kind,
        title=PurePosixPath(safe).name,
        owner=user.id,
        scope=store.scope.prefix,
        work=work,
        on_settled=on_settled,
    )


def _bulk_scope(user: User, paths: list[str]) -> str | None:
    """Resolve the common scope of a bulk selection for job attribution.

    A selection always comes from one scope's view, so the first entry names it
    and resolving it doubles as an early access check; an empty list has no scope.
    """
    if not paths:
        return None
    store, _ = resolve_workspace_path(user, paths[0], write=True)
    return store.scope.prefix


def _submit_bulk_job(
    *,
    user: User,
    kind: DocumentJobKind,
    title: str,
    files: list[str],
    process_one: Callable[[str], Awaitable[None]],
    verb: str,
) -> JobView:
    """Submit a bulk per-file mutation as a single background job."""
    scope = _bulk_scope(user, files)

    async def work(ctx: JobContext) -> None:
        await run_bulk_document_job(files, process_one, verb=verb, ctx=ctx)

    return manager.submit(kind=kind, title=title, owner=user.id, scope=scope, work=work)


@router.get("/documents/original/{filepath:path}")
async def download_original(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Download the original binary file for a document."""
    store, safe = resolve_workspace_path(user, filepath)
    original = await find_original(store, safe)
    media_type = mimetypes.guess_type(original.name)[0] or "application/octet-stream"
    return FileResponse(
        path=original,
        media_type=media_type,
        headers={"Content-Disposition": attachment_disposition(original.name)},
    )


@router.put("/documents/original/{filepath:path}")
async def replace_original(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> JobView:
    """Replace the original binary file and reconvert the document as a job.

    Like :func:`upload`, the bytes are spooled and the reconversion runs off
    the request, observable and cancellable through the ``/jobs`` endpoints.
    """
    store, safe = resolve_workspace_path(user, filepath, write=True)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))
    new_filename = file.filename
    spool = await _spool_payload(
        file, limit=settings.limits.max_file_size_bytes, label="File"
    )

    async def work(ctx: JobContext) -> None:
        content = await asyncio.to_thread(spool.read_bytes)
        await workspace.replace_original(
            store, safe, content, new_filename=new_filename, spec=spec, llm=llm, ctx=ctx
        )

    return _submit_document_job(
        user=user,
        store=store,
        safe=safe,
        kind=DocumentJobKind.REPLACE_ORIGINAL,
        work=work,
        on_settled=lambda: spool.unlink(missing_ok=True),
    )


@router.put("/documents/{filepath:path}")
async def upload_document(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
    overwrite: bool = Form(default=False),
) -> JobView:
    """Accept a document upload and process it as a background job.

    Returns the job's initial snapshot as soon as the bytes are received;
    conversion and indexing then run off the request, observable and
    cancellable through the ``/jobs`` endpoints, so the UI stays usable and
    closing the tab no longer aborts the work.
    """
    store, safe = resolve_workspace_path(user, filepath, write=True)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))
    spool = await _spool_payload(
        file, limit=settings.limits.max_file_size_bytes, label="File"
    )

    async def work(ctx: JobContext) -> None:
        content = await asyncio.to_thread(spool.read_bytes)
        await workspace.upload(
            store, safe, content, spec=spec, llm=llm, overwrite=overwrite, ctx=ctx
        )

    return _submit_document_job(
        user=user,
        store=store,
        safe=safe,
        kind=DocumentJobKind.UPLOAD,
        work=work,
        on_settled=lambda: spool.unlink(missing_ok=True),
    )


@router.post("/documents/collections/{target:path}")
async def upload_collection(
    target: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> JobView:
    """Accept a ZIP collection and process it as a background job.

    Each file flows through the phased upload, so the job reports per-file
    progress and the workspace stays usable while a large archive processes.
    ``target`` is a canonical directory — a scope root (``~``, ``@<group>``) or a
    subdir under it — so a drop can target a folder, not just the workspace root;
    it resolves to the store and destination subdir like every other item route.
    """
    spec, resolved = await validate_collection_upload(pipeline_spec, llm_config)
    store, dest = resolve_workspace_path(user, target, write=True)
    spool = await _spool_payload(
        file, limit=settings.limits.max_collection_size_bytes, label="Collection"
    )
    # Enforce every collection limit here, before the job is queued, so a
    # too-large, too-many-files, or malformed archive is rejected synchronously
    # with a clear reason instead of failing the job much later.
    try:
        await asyncio.to_thread(workspace.validate_collection_archive, spool)
    except BaseException:
        spool.unlink(missing_ok=True)
        raise

    async def work(ctx: JobContext) -> None:
        # The ZIP is read straight from the spool file, so a large archive is
        # never loaded into memory whole.
        async for event in workspace.process_collection(
            store, spool, spec, resolved, dest_dir=dest
        ):
            if isinstance(event, CollectionProgressEvent):
                ctx.set_progress(event.current, event.total)
            elif isinstance(event, CollectionCompleteEvent):
                ctx.set_stage(event.message)
                # The complete event is terminal, so this ends the loop.  A
                # per-file failure is not fatal to the batch, but the job must
                # not settle as a clean success: fail it with the per-reason
                # breakdown so the tray shows why, while succeeded files stay
                # committed.
                if event.failed_files:
                    raise RuntimeError(
                        f"{len(event.failed_files)} file(s) not imported. "
                        f"{summarize_failed_files(event.failed_files)}"
                    )

    return manager.submit(
        kind=DocumentJobKind.COLLECTION,
        title=file.filename or "collection.zip",
        owner=user.id,
        scope=store.scope.prefix,
        work=work,
        on_settled=lambda: spool.unlink(missing_ok=True),
    )


@router.delete("/documents/{scope}")
async def delete_all_documents(
    scope: str,
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteDocumentsResponse:
    """Delete all documents, chunks, and originals in a workspace."""
    store, _ = resolve_workspace_path(user, scope, write=True)
    await workspace.delete_all(store)
    return BulkDeleteDocumentsResponse(
        message="All documents and search index deleted successfully",
    )


@router.post("/documents/rechunk/bulk")
async def bulk_rechunk(
    request: BulkRechunkRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> JobView:
    """Rechunk multiple documents as a single background job."""
    spec = request.pipeline

    async def _rechunk_one(filepath: str) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.rechunk(store, safe, spec=spec)

    return _submit_bulk_job(
        user=user,
        kind=DocumentJobKind.RECHUNK_BULK,
        title=f"Rechunk {_plural(len(request.files), 'document')}",
        files=request.files,
        process_one=_rechunk_one,
        verb="Rechunked",
    )


@router.post("/documents/reconvert/bulk")
async def bulk_reconvert(
    request: BulkReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> JobView:
    """Reconvert multiple documents as a single background job."""
    spec = request.pipeline
    resolved = await prepare_llm_config(request.llm)

    async def _reconvert_one(filepath: str) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.reconvert(store, safe, spec=spec, llm=resolved)

    return _submit_bulk_job(
        user=user,
        kind=DocumentJobKind.RECONVERT_BULK,
        title=f"Reconvert {_plural(len(request.files), 'document')}",
        files=request.files,
        process_one=_reconvert_one,
        verb="Reconverted",
    )


@router.post("/documents/move/bulk")
async def bulk_move(
    request: BulkMoveRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> JobView:
    """Move multiple documents as a single background job."""
    destinations = {m.source: m.destination for m in request.moves}
    sources = list(destinations)
    moved: dict[str, tuple[Casebase, list[str]]] = {}

    async def _move_one(filepath: str) -> None:
        src_store, src, dst_store, dst = resolve_move(
            user, filepath, destinations[filepath]
        )
        await workspace.move_document(src_store, dst_store, src, dst)
        moved.setdefault(src_store.store_key, (src_store, []))[1].append(src)

    scope = _bulk_scope(user, sources)

    async def work(ctx: JobContext) -> None:
        try:
            await run_bulk_document_job(sources, _move_one, verb="Moved", ctx=ctx)
        finally:
            # Per-entry moves leave their emptied source directories behind.
            # Prune them even when the batch failed or was cancelled — so the
            # client's refresh sees final state — and shield the prune so a
            # cancel cannot interrupt it midway.
            for store, srcs in moved.values():
                await shield_to_completion(workspace.prune_empty_dirs(store, srcs))

    return manager.submit(
        kind=DocumentJobKind.MOVE_BULK,
        title=f"Move {_plural(len(sources), 'document')}",
        owner=user.id,
        scope=scope,
        work=work,
    )


@router.post("/documents/delete/bulk")
async def bulk_delete(
    request: BulkDeleteRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> JobView:
    """Delete multiple documents as a single background job."""

    async def _delete_one(filepath: str) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.delete_document(store, safe)

    return _submit_bulk_job(
        user=user,
        kind=DocumentJobKind.DELETE_BULK,
        title=f"Delete {_plural(len(request.files), 'document')}",
        files=request.files,
        process_one=_delete_one,
        verb="Deleted",
    )


@router.post("/documents/line-counts")
async def get_document_line_counts(
    request: DocumentLineCountsRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentLineCountsResponse:
    """Batch-resolve document line counts for the context-panel coverage map.

    Each requested path is resolved to its store (skipping web URLs and any the
    caller cannot reach), grouped per store, and looked up in one query each.
    Unknown or not-yet-indexed paths are simply omitted from the response.
    """
    grouped: dict[Casebase, dict[str, str]] = {}
    for original in request.files:
        try:
            store, safe = resolve_workspace_path(user, original)
        except HTTPException:
            continue
        if not safe:
            continue
        grouped.setdefault(store, {})[safe] = original

    line_counts: dict[str, int] = {}
    for store, safe_to_original in grouped.items():
        counts = await get_line_counts(store, list(safe_to_original))
        for safe, count in counts.items():
            line_counts[safe_to_original[safe]] = count

    return DocumentLineCountsResponse(line_counts=line_counts)


@router.get("/documents/chunks/{filepath:path}")
async def get_document_chunks(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentMetadata:
    """Get chunks for a document."""
    store, safe = resolve_workspace_path(user, filepath)
    chunked = await get_document(store, safe)
    if not chunked:
        raise HTTPException(status_code=404, detail="No chunks found for this document")
    return chunked


@router.post("/documents/rechunk/{filepath:path}")
async def rechunk_document(
    filepath: str,
    request: PipelineSpec,
    user: Annotated[User, Depends(get_current_user)],
) -> JobView:
    """Re-chunk a document with different settings as a background job."""
    store, safe = resolve_workspace_path(user, filepath, write=True)

    async def work(ctx: JobContext) -> None:
        ctx.set_stage("Chunking document")
        await workspace.rechunk(store, safe, spec=request)

    return _submit_document_job(
        user=user, store=store, safe=safe, kind=DocumentJobKind.RECHUNK, work=work
    )


@router.post("/documents/reconvert/{filepath:path}")
async def reconvert_document(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> JobView:
    """Re-convert a document from its original binary file as a background job."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    resolved = await prepare_llm_config(request.llm)

    async def work(ctx: JobContext) -> None:
        await workspace.reconvert(
            store, safe, spec=request.pipeline, llm=resolved, ctx=ctx
        )

    return _submit_document_job(
        user=user, store=store, safe=safe, kind=DocumentJobKind.RECONVERT, work=work
    )


@router.post("/documents/move/{filepath:path}")
async def move_document(
    filepath: str,
    request: MoveDocumentRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDocumentResponse:
    """Move a document within a workspace or migrate it to another.

    ``filepath`` and ``destination`` are canonical paths; resolving both with
    ``write=True`` requires write access to each end, so a cross-workspace move
    is allowed exactly when the caller may write both the source and the
    destination.
    """
    src_store, src, dst_store, dst = resolve_move(user, filepath, request.destination)
    return await workspace.move_document(src_store, dst_store, src, dst)


@router.get("/documents/assets/{filepath:path}")
async def list_document_assets(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetListResponse:
    """List assets for a document."""
    store, safe = resolve_workspace_path(user, filepath)
    return await asyncio.to_thread(list_assets, store, safe)


@router.patch("/documents/assets/{filepath:path}")
async def patch_asset_description(
    filepath: str,
    request: UpdateAssetDescriptionRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetEntry:
    """Update an asset's companion .md description."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    return await workspace.update_asset_description(
        store, safe, request.asset_name, request.content
    )


@router.post("/documents/assets/{filepath:path}")
async def generate_asset_description(
    filepath: str,
    request: GenerateAssetDescriptionRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetEntry:
    """Generate an asset's companion .md description with the vision model."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    llm = await prepare_llm_config(request.llm)
    return await workspace.generate_asset_description(
        store, safe, request.asset_name, llm
    )


@router.delete("/documents/assets/{filepath:path}")
async def delete_asset_description(
    filepath: str,
    asset_name: str,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetEntry:
    """Delete an asset's companion .md description, keeping the asset itself."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    return await workspace.delete_asset_description(store, safe, asset_name)


# Registered after the more specific /documents/assets/ PATCH route so the
# catch-all path parameter cannot shadow it.
@router.patch("/documents/{filepath:path}")
async def write_document(
    filepath: str,
    request: WriteDocumentRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> WriteDocumentResponse:
    """Replace a text document's content in place.

    Unlike the PUT upload route this keeps the entry's original binary,
    assets, and provenance, and only rewrites the markdown and its chunks.
    """
    store, safe = resolve_workspace_path(user, filepath, write=True)
    message = await workspace.write_document_text(
        store, safe, request.content, mode=request.mode, chunking=request.chunking
    )
    return WriteDocumentResponse(filename=safe, message=message)


@router.get("/documents/{filepath:path}")
async def get_document_content(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Get the content of a document or asset."""
    store, safe = resolve_workspace_path(user, filepath)
    return await get_document_response(store, safe)


@router.delete("/documents/{filepath:path}")
async def delete_document(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDocumentResponse:
    """Delete a document and its associated chunks and original."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    await workspace.delete_document(store, safe)
    return DeleteDocumentResponse(
        filename=safe,
        message="Document deleted successfully",
    )
