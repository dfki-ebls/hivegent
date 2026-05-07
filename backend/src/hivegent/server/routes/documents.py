"""Routes for user document and collection management."""

import mimetypes
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.sse import EventSourceResponse
from starlette.responses import Response

from ... import workspace
from ...auth import User, get_current_user
from ...chunks import DocumentMetadata, get_metadata
from ...config import settings
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
    DocumentListResponse,
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
from ..common import parse_pipeline_spec, resolve_llm_config, safe_path, user_store
from ..operations import (
    PreparedCollection,
    find_original,
    get_document_response,
    list_assets,
    list_documents_for_store,
    prepare_collection_upload,
    process_bulk_operation,
    read_collection_zip,
    reconvert_single_stream,
    upload_file_stream,
    validate_collection_upload,
)
from ..models import (
    BulkDeleteRequest,
    BulkRechunkRequest,
    BulkReconvertRequest,
    ReconvertRequest,
)

__all__ = ["router"]

router = APIRouter()


@router.get("/documents")
async def list_documents(
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentListResponse:
    """List all documents in the user's data directory."""
    return list_documents_for_store(user_store(user))


@router.get("/documents/original/{filepath:path}")
async def download_original(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Download the original binary file for a document."""
    safe = safe_path(filepath)
    original = find_original(user_store(user), safe)
    content = original.read_bytes()
    media_type = mimetypes.guess_type(original.name)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{original.name}"'},
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
    safe = safe_path(filepath)
    store = user_store(user)
    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

    spec = parse_pipeline_spec(pipeline_spec)
    llm = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.aux_model,
    )
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
    safe = safe_path(filepath)
    store = user_store(user)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.aux_model,
    )

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

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
    safe = safe_path(filepath)
    store = user_store(user)
    spec = parse_pipeline_spec(pipeline_spec)
    llm = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.aux_model,
    )

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

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
    safe = safe_path(filepath)
    store = user_store(user)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.aux_model)
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
    safe = safe_path(filepath)
    store = user_store(user)
    yield OperationStageEvent(stage="Chunking document")
    try:
        result = await workspace.rechunk(store, safe, spec=request)
        yield RechunkCompleteEvent(
            pipeline=result.pipeline,
            chunk_count=len(result.chunks),
        )
    except HTTPException as exc:
        yield OperationErrorEvent(detail=str(exc.detail))
    except Exception as exc:
        yield OperationErrorEvent(detail=f"Chunking failed: {exc!s}")


@router.post("/documents/collections")
async def upload_collection(
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> CollectionUploadResponse:
    """Upload a markdown collection as a ZIP archive."""
    spec, resolved = validate_collection_upload(pipeline_spec, llm_config)
    store = user_store(user)
    raw = await read_collection_zip(file)

    result: CollectionCompleteEvent | None = None
    async for event in workspace.process_collection(store, raw, spec, resolved):
        if isinstance(event, CollectionCompleteEvent):
            result = event

    assert result is not None
    return result


@router.post("/documents/collections/stream", response_class=EventSourceResponse)
async def upload_collection_stream(
    user: Annotated[User, Depends(get_current_user)],
    prepared: Annotated[PreparedCollection, Depends(prepare_collection_upload)],
) -> AsyncIterable[CollectionProgressEvent | CollectionCompleteEvent]:
    """Upload a collection with streaming progress events."""
    store = user_store(user)
    async for event in workspace.process_collection(
        store, prepared.raw, prepared.spec, prepared.resolved
    ):
        yield event


@router.delete("/documents")
async def delete_all_documents(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteDocumentsResponse:
    """Delete all documents, chunks, originals, and the search index."""
    await workspace.delete_all(user_store(user))
    return BulkDeleteDocumentsResponse(
        message="All documents and search index deleted successfully",
    )


@router.post("/documents/rechunk/bulk/stream", response_class=EventSourceResponse)
async def bulk_rechunk_stream(
    request: BulkRechunkRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Bulk rechunk multiple documents with streaming progress."""
    store = user_store(user)
    spec = request.pipeline

    async def _rechunk_one(filepath: str) -> None:
        await workspace.rechunk(store, safe_path(filepath), spec=spec, sync=False)

    async for event in process_bulk_operation(
        store, request.files, _rechunk_one, "Rechunked"
    ):
        yield event


@router.post("/documents/reconvert/bulk/stream", response_class=EventSourceResponse)
async def bulk_reconvert_stream(
    request: BulkReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Bulk reconvert multiple documents with streaming progress."""
    store = user_store(user)
    spec = request.pipeline
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.aux_model)

    async def _reconvert_one(filepath: str) -> None:
        await workspace.reconvert(
            store, safe_path(filepath), spec=spec, llm=resolved, sync=False
        )

    async for event in process_bulk_operation(
        store, request.files, _reconvert_one, "Reconverted"
    ):
        yield event


@router.post("/documents/delete/bulk/stream", response_class=EventSourceResponse)
async def bulk_delete_stream(
    request: BulkDeleteRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Bulk delete multiple documents with streaming progress."""
    store = user_store(user)

    async def _delete_one(filepath: str) -> None:
        await workspace.delete_document(store, safe_path(filepath), sync=False)

    async for event in process_bulk_operation(
        store, request.files, _delete_one, "Deleted"
    ):
        yield event


@router.get("/documents/chunks/{filepath:path}")
async def get_document_chunks(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentMetadata:
    """Get chunks for a document."""
    safe = safe_path(filepath)
    chunked = get_metadata(user_store(user), safe)
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
    safe = safe_path(filepath)
    store = user_store(user)
    return await workspace.rechunk(store, safe, spec=request)


@router.post("/documents/reconvert/{filepath:path}")
async def reconvert_document(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Re-convert a document from its original binary file."""
    safe = safe_path(filepath)
    store = user_store(user)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.aux_model)
    return await workspace.reconvert(store, safe, spec=request.pipeline, llm=resolved)


@router.post("/documents/move/{filepath:path}")
async def move_document(
    filepath: str,
    request: MoveDocumentRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDocumentResponse:
    """Move a document to a new location."""
    src = safe_path(filepath)
    dst = safe_path(request.destination)
    if src == dst:
        raise HTTPException(
            status_code=400, detail="Source and destination are the same"
        )
    return await workspace.move_document(user_store(user), src, dst)


@router.get("/documents/assets/{filepath:path}")
async def list_document_assets(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetListResponse:
    """List assets for a document."""
    safe = safe_path(filepath)
    return list_assets(user_store(user), safe)


@router.patch("/documents/assets/{filepath:path}")
async def patch_asset_description(
    filepath: str,
    request: UpdateAssetDescriptionRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetEntry:
    """Update an asset's companion .md description."""
    safe = safe_path(filepath)
    return await workspace.update_asset_description(
        user_store(user), safe, request.asset_name, request.content
    )


@router.get("/documents/{filepath:path}")
async def get_document_content(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Get the content of a document or asset."""
    safe = safe_path(filepath)
    return get_document_response(user_store(user), safe)


@router.delete("/documents/{filepath:path}")
async def delete_document(
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDocumentResponse:
    """Delete a document and its associated chunks and original."""
    safe = safe_path(filepath)
    await workspace.delete_document(user_store(user), safe)
    return DeleteDocumentResponse(
        filename=safe,
        message="Document deleted successfully",
    )
