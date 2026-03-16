"""Routes for user document and collection management."""

import mimetypes
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from starlette.responses import Response, StreamingResponse

from ...auth import User, get_current_user
from ...chunks import DocumentMetadata, chunk_document, get_metadata
from ...config import settings
from ...retrieval import invalidate_store, mark_dirty
from ...types import (
    BulkDeleteDocumentsResponse,
    CollectionCompleteEvent,
    CollectionUploadResponse,
    DeleteDocumentResponse,
    DocumentListResponse,
    LlmConfig,
    MoveDocumentRequest,
    MoveDocumentResponse,
    OperationErrorEvent,
    OperationStageEvent,
    RechunkCompleteEvent,
    UploadDocumentResponse,
)
from ..common import parse_pipeline_spec, resolve_llm_config, safe_path, user_store
from ..operations import (
    collection_stream_response,
    delete_single,
    find_original,
    get_document_response,
    list_documents_for_store,
    move_document_internal,
    process_bulk_operation,
    process_collection,
    read_collection_zip,
    reconvert_single,
    reconvert_single_stream,
    sse_stream_response,
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
    original = find_original(store, safe)

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

    original.write_bytes(content)
    spec = parse_pipeline_spec(pipeline_spec)
    llm_config_model = resolve_llm_config(
        LlmConfig.model_validate_json(llm_config),
        default_model=settings.llm.vision_model,
    )
    result = await reconvert_single(store, safe, spec, llm_config_model)
    mark_dirty(store)
    return result


@router.put("/documents/{filepath:path}")
async def upload_document(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> UploadDocumentResponse:
    """Upload or replace a document."""
    safe = safe_path(filepath)
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
        store=user_store(user),
        filepath=safe,
        content=content,
        spec=spec,
        llm_config=llm_config_model,
    )


@router.put("/documents/stream/{filepath:path}")
async def upload_document_stream(
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> StreamingResponse:
    """Upload or replace a document with streaming progress events."""
    safe = safe_path(filepath)
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

    return sse_stream_response(
        upload_file_stream(
            store=user_store(user),
            filepath=safe,
            content=content,
            spec=spec,
            llm_config=llm_config_model,
        )
    )


@router.post("/documents/reconvert/stream/{filepath:path}")
async def reconvert_document_stream(
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Re-convert a document with streaming progress events."""
    safe = safe_path(filepath)
    store = user_store(user)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)
    return sse_stream_response(
        reconvert_single_stream(store, safe, request.pipeline, resolved)
    )


@router.post("/documents/rechunk/stream/{filepath:path}")
async def rechunk_document_stream(
    filepath: str,
    request: PipelineSpec,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Re-chunk a document with streaming progress events."""
    safe = safe_path(filepath)
    store = user_store(user)
    file_path = store.workspace_dir(settings.data_dir) / safe
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    text_content = file_path.read_text(encoding="utf-8")

    async def _rechunk_stream():  # type: ignore[return]
        yield OperationStageEvent(stage="Chunking document")
        try:
            result = await chunk_document(store, safe, text_content, request.chunking)
            mark_dirty(store)
            yield RechunkCompleteEvent(
                pipeline=result.pipeline,
                chunk_count=len(result.chunks),
            )
        except Exception as exc:
            yield OperationErrorEvent(detail=f"Chunking failed: {exc!s}")

    return sse_stream_response(_rechunk_stream())


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


@router.post("/documents/collections/stream")
async def upload_collection_stream(
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> StreamingResponse:
    """Upload a collection with streaming progress events."""
    spec, resolved = validate_collection_upload(pipeline_spec, llm_config)
    store = user_store(user)
    raw = await read_collection_zip(file)
    return collection_stream_response(store, raw, spec, resolved)


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
        store.originals_dir,
        store.lancedb_dir,
    ):
        directory = directory_fn(data_dir)
        if directory.exists():
            shutil.rmtree(directory)

    invalidate_store(store)
    return BulkDeleteDocumentsResponse(
        message="All documents and search index deleted successfully",
    )


@router.post("/documents/rechunk/bulk/stream")
async def bulk_rechunk_stream(
    request: BulkRechunkRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Bulk rechunk multiple documents with streaming progress."""
    store = user_store(user)
    workspace = store.workspace_dir(settings.data_dir)
    spec = request.pipeline

    async def _rechunk_one(filepath: str) -> None:
        safe = safe_path(filepath)
        text_content = (workspace / safe).read_text(encoding="utf-8")
        await chunk_document(store, safe, text_content, spec.chunking)

    return sse_stream_response(
        process_bulk_operation(store, request.files, _rechunk_one, "Rechunked"),
    )


@router.post("/documents/reconvert/bulk/stream")
async def bulk_reconvert_stream(
    request: BulkReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Bulk reconvert multiple documents with streaming progress."""
    store = user_store(user)
    spec = request.pipeline
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)

    async def _reconvert_one(filepath: str) -> None:
        safe = safe_path(filepath)
        await reconvert_single(store, safe, spec, resolved)

    return sse_stream_response(
        process_bulk_operation(store, request.files, _reconvert_one, "Reconverted"),
    )


@router.post("/documents/delete/bulk/stream")
async def bulk_delete_stream(
    request: BulkDeleteRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Bulk delete multiple documents with streaming progress."""
    store = user_store(user)

    async def _delete_one(filepath: str) -> None:
        safe = safe_path(filepath)
        delete_single(store, safe)

    return sse_stream_response(
        process_bulk_operation(store, request.files, _delete_one, "Deleted"),
    )


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
        mark_dirty(store)
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
    mark_dirty(store)
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
    mark_dirty(store)
    return DeleteDocumentResponse(
        filename=safe,
        message="Document deleted successfully",
    )
