"""Routes for user document and collection management."""

import mimetypes
import shutil
from collections.abc import AsyncIterable
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.sse import EventSourceResponse
from starlette.responses import Response

from ...auth import User, get_current_user
from ...chunks import DocumentMetadata, chunk_document, get_metadata
from ...config import settings
from ...entries import stem_path_from_reference
from ...retrieval import invalidate_store, mark_dirty_and_sync
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
    RechunkCompleteEvent,
    UpdateAssetDescriptionRequest,
    UploadCompleteEvent,
    UploadDocumentResponse,
)
from ..common import parse_pipeline_spec, resolve_llm_config, safe_path, user_store
from ..operations import (
    delete_single,
    PreparedCollection,
    ensure_upload_slot,
    find_original,
    get_document_response,
    list_assets,
    list_documents_for_store,
    move_document_internal,
    prepare_collection_upload,
    process_bulk_operation,
    process_collection,
    read_collection_zip,
    reconvert_single,
    reconvert_single_stream,
    update_asset_description,
    upload_file,
    upload_file_stream,
    validate_collection_upload,
)
from ..models import (
    BulkDeleteRequest,
    BulkRechunkRequest,
    BulkReconvertRequest,
    PipelineSpec,
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
    metadata = get_metadata(store, safe)
    original = find_original(store, safe)

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

    new_suffix = PurePosixPath(file.filename or original.name).suffix or original.suffix
    new_original_relpath = f"{stem_path_from_reference(safe)}{new_suffix.lower()}"
    new_original_path = store.workspace_dir(settings.data_dir) / new_original_relpath
    if (
        metadata
        and metadata.original_path
        and metadata.original_path != new_original_relpath
    ):
        original.unlink(missing_ok=True)
        new_original_path.parent.mkdir(parents=True, exist_ok=True)
    new_original_path.write_bytes(content)

    spec = parse_pipeline_spec(pipeline_spec)
    llm_config_model = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.vision_model,
    )
    result = await upload_file(
        store=store,
        filepath=new_original_relpath,
        content=content,
        spec=spec,
        llm_config=llm_config_model,
        origin=metadata.origin if metadata else "upload",
        sync=False,
    )
    mark_dirty_and_sync(store)
    return result


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
    ensure_upload_slot(store, safe, overwrite=overwrite)

    spec = parse_pipeline_spec(pipeline_spec)
    llm_config_model = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.vision_model,
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
        llm_config=llm_config_model,
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
    ensure_upload_slot(store, safe, overwrite=overwrite)

    spec = parse_pipeline_spec(pipeline_spec)
    llm_config_model = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.vision_model,
    )

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

    return await upload_file(
        store=store,
        filepath=safe,
        content=content,
        spec=spec,
        llm_config=llm_config_model,
    )


@router.post("/documents/reconvert/stream/{filepath:path}", response_class=EventSourceResponse)
async def reconvert_document_stream(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
    """Re-convert a document with streaming progress events."""
    safe = safe_path(filepath)
    store = user_store(user)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)
    async for event in reconvert_single_stream(store, safe, request.pipeline, resolved):
        yield event


@router.post("/documents/rechunk/stream/{filepath:path}", response_class=EventSourceResponse)
async def rechunk_document_stream(
    filepath: str,
    request: PipelineSpec,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[OperationStageEvent | RechunkCompleteEvent | OperationErrorEvent]:
    """Re-chunk a document with streaming progress events."""
    safe = safe_path(filepath)
    store = user_store(user)
    file_path = store.workspace_dir(settings.data_dir) / safe
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    text_content = file_path.read_text(encoding="utf-8")
    yield OperationStageEvent(stage="Chunking document")
    try:
        result = await chunk_document(store, safe, text_content, request.chunking)
        mark_dirty_and_sync(store)
        yield RechunkCompleteEvent(
            pipeline=result.pipeline,
            chunk_count=len(result.chunks),
        )
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
    async for event in process_collection(store, raw, spec, resolved):
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
    async for event in process_collection(store, prepared.raw, prepared.spec, prepared.resolved):
        yield event


@router.delete("/documents")
async def delete_all_documents(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteDocumentsResponse:
    """Delete all documents, chunks, originals, and the search index."""
    store = user_store(user)
    data_dir = settings.data_dir
    for directory_fn in (
        store.workspace_dir,
        store.metadata_dir,
        store.lancedb_dir,
    ):
        directory = directory_fn(data_dir)
        if directory.exists():
            shutil.rmtree(directory)

    invalidate_store(store)
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
    workspace = store.workspace_dir(settings.data_dir)
    spec = request.pipeline

    async def _rechunk_one(filepath: str) -> None:
        safe = safe_path(filepath)
        text_content = (workspace / safe).read_text(encoding="utf-8")
        await chunk_document(store, safe, text_content, spec.chunking)

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
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)

    async def _reconvert_one(filepath: str) -> None:
        safe = safe_path(filepath)
        await reconvert_single(store, safe, spec, resolved)

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
        safe = safe_path(filepath)
        delete_single(store, safe)

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
    file_path = store.workspace_dir(settings.data_dir) / safe
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    text_content = file_path.read_text(encoding="utf-8")
    try:
        result = await chunk_document(store, safe, text_content, request.chunking)
        mark_dirty_and_sync(store)
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Chunking failed: {exc!s}",
        ) from exc


@router.post("/documents/reconvert/{filepath:path}")
async def reconvert_document(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Re-convert a document from its original binary file."""
    safe = safe_path(filepath)
    store = user_store(user)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)
    result = await reconvert_single(store, safe, request.pipeline, resolved)
    mark_dirty_and_sync(store)
    return result


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
    return move_document_internal(user_store(user), src, dst)


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
    return update_asset_description(
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
    store = user_store(user)
    delete_single(store, safe)
    mark_dirty_and_sync(store)
    return DeleteDocumentResponse(
        filename=safe,
        message="Document deleted successfully",
    )
