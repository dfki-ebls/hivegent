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
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.sse import EventSourceResponse
from starlette.responses import FileResponse, Response

from ... import workspace
from ...auth import User, get_current_user
from ...chunkers.base import DocumentMetadata
from ...db.documents import get_document
from ...types import (
    AssetEntry,
    AssetListResponse,
    BulkDeleteDocumentsResponse,
    BulkOperationCompleteEvent,
    BulkOperationProgressEvent,
    CollectionCompleteEvent,
    CollectionProgressEvent,
    CollectionUploadResponse,
    DeleteDocumentResponse,
    GenerateAssetDescriptionRequest,
    LlmConfig,
    MoveDocumentRequest,
    MoveDocumentResponse,
    OperationErrorEvent,
    OperationStageEvent,
    PipelineSpec,
    RechunkCompleteEvent,
    UpdateAssetDescriptionRequest,
    UploadCompleteEvent,
    UploadDocumentResponse,
)
from ..common import (
    parse_pipeline_spec,
    prepare_llm_config,
    resolve_workspace_path,
)
from ..models import (
    BulkDeleteRequest,
    BulkRechunkRequest,
    BulkReconvertRequest,
    ReconvertRequest,
)
from ..operations import (
    PreparedCollection,
    attachment_disposition,
    find_original,
    get_document_response,
    list_assets,
    prepare_collection_upload,
    process_bulk_operation,
    read_collection_zip,
    read_upload_file,
    reconvert_single_stream,
    upload_file_stream,
    validate_collection_upload,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


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
) -> UploadDocumentResponse:
    """Replace the original binary file and reconvert the document."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    content = await read_upload_file(file)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))
    return await workspace.replace_original(
        store,
        safe,
        content,
        new_filename=file.filename,
        spec=spec,
        llm=llm,
    )


@router.put("/documents/stream/{filepath:path}", response_class=EventSourceResponse)
async def upload_document_stream(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
    overwrite: bool = Form(default=False),
) -> AsyncIterable[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
    """Upload or replace a document with streaming progress events."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))

    content = await read_upload_file(file)
    async for event in upload_file_stream(
        store=store,
        filepath=safe,
        content=content,
        spec=spec,
        llm_config=llm,
        overwrite=overwrite,
    ):
        yield event


@router.put("/documents/{filepath:path}")
async def upload_document(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
    overwrite: bool = Form(default=False),
) -> UploadDocumentResponse:
    """Upload or replace a document."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))

    content = await read_upload_file(file)
    return await workspace.upload(
        store,
        safe,
        content,
        spec=spec,
        llm=llm,
        overwrite=overwrite,
    )


@router.post(
    "/documents/reconvert/stream/{filepath:path}", response_class=EventSourceResponse
)
async def reconvert_document_stream(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
    """Re-convert a document with streaming progress events."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    resolved = await prepare_llm_config(request.llm)
    async for event in reconvert_single_stream(store, safe, request.pipeline, resolved):
        yield event


@router.post(
    "/documents/rechunk/stream/{filepath:path}", response_class=EventSourceResponse
)
async def rechunk_document_stream(
    filepath: str,
    request: PipelineSpec,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[OperationStageEvent | RechunkCompleteEvent | OperationErrorEvent]:
    """Re-chunk a document with streaming progress events."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    yield OperationStageEvent(stage="Chunking document")
    try:
        result = await workspace.rechunk(store, safe, spec=request)
        yield RechunkCompleteEvent(
            pipeline=result.pipeline,
            chunk_count=len(result.chunks),
        )
    except HTTPException as exc:
        yield OperationErrorEvent(detail=str(exc.detail))
    except Exception:
        logger.exception("Chunking failed for %s", safe)
        yield OperationErrorEvent(detail="Chunking failed")


@router.post("/documents/collections/{scope}")
async def upload_collection(
    scope: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> CollectionUploadResponse:
    """Upload a markdown collection as a ZIP archive."""
    spec, resolved = await validate_collection_upload(pipeline_spec, llm_config)
    store, _ = resolve_workspace_path(user, scope, write=True)
    raw = await read_collection_zip(file)

    result: CollectionCompleteEvent | None = None
    async for event in workspace.process_collection(store, raw, spec, resolved):
        if isinstance(event, CollectionCompleteEvent):
            result = event

    assert result is not None
    return result


@router.post(
    "/documents/collections/stream/{scope}", response_class=EventSourceResponse
)
async def upload_collection_stream(
    scope: str,
    user: Annotated[User, Depends(get_current_user)],
    prepared: Annotated[PreparedCollection, Depends(prepare_collection_upload)],
) -> AsyncIterable[CollectionProgressEvent | CollectionCompleteEvent]:
    """Upload a collection with streaming progress events."""
    store, _ = resolve_workspace_path(user, scope, write=True)
    async for event in workspace.process_collection(
        store, prepared.raw, prepared.spec, prepared.resolved
    ):
        yield event


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


@router.post("/documents/rechunk/bulk/stream", response_class=EventSourceResponse)
async def bulk_rechunk_stream(
    request: BulkRechunkRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Bulk rechunk multiple documents with streaming progress."""
    spec = request.pipeline

    async def _rechunk_one(filepath: str) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.rechunk(store, safe, spec=spec)

    async for event in process_bulk_operation(request.files, _rechunk_one, "Rechunked"):
        yield event


@router.post("/documents/reconvert/bulk/stream", response_class=EventSourceResponse)
async def bulk_reconvert_stream(
    request: BulkReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Bulk reconvert multiple documents with streaming progress."""
    spec = request.pipeline
    resolved = await prepare_llm_config(request.llm)

    async def _reconvert_one(filepath: str) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.reconvert(store, safe, spec=spec, llm=resolved)

    async for event in process_bulk_operation(
        request.files, _reconvert_one, "Reconverted"
    ):
        yield event


@router.post("/documents/delete/bulk/stream", response_class=EventSourceResponse)
async def bulk_delete_stream(
    request: BulkDeleteRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Bulk delete multiple documents with streaming progress."""

    async def _delete_one(filepath: str) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.delete_document(store, safe)

    async for event in process_bulk_operation(request.files, _delete_one, "Deleted"):
        yield event


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
) -> DocumentMetadata:
    """Re-chunk a document with different settings."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    return await workspace.rechunk(store, safe, spec=request)


@router.post("/documents/reconvert/{filepath:path}")
async def reconvert_document(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Re-convert a document from its original binary file."""
    store, safe = resolve_workspace_path(user, filepath, write=True)
    resolved = await prepare_llm_config(request.llm)
    return await workspace.reconvert(store, safe, spec=request.pipeline, llm=resolved)


@router.post("/documents/move/{filepath:path}")
async def move_document(
    filepath: str,
    request: MoveDocumentRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDocumentResponse:
    """Move a document to a new location within the same workspace."""
    src_store, src = resolve_workspace_path(user, filepath, write=True)
    dst_store, dst = resolve_workspace_path(user, request.destination, write=True)
    if src_store.store_key != dst_store.store_key:
        raise HTTPException(
            status_code=400, detail="Cannot move a document across workspaces"
        )
    return await workspace.move_document(src_store, src, dst)


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
