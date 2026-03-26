"""Routes for group document and directory access."""

from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.sse import EventSourceResponse
from starlette.responses import Response

from ...auth import User, get_current_user
from ...config import settings
from ...retrieval import mark_dirty_and_sync
from ...types import (
    AssetEntry,
    AssetListResponse,
    CollectionCompleteEvent,
    CollectionProgressEvent,
    CollectionUploadResponse,
    CreateDirectoryRequest,
    CreateDirectoryResponse,
    DeleteDirectoryRequest,
    DeleteDirectoryResponse,
    DeleteDocumentResponse,
    DirectoryTreeResponse,
    DocumentListResponse,
    LlmConfig,
    MoveDocumentRequest,
    MoveDocumentResponse,
    OperationErrorEvent,
    OperationStageEvent,
    UpdateAssetDescriptionRequest,
    UploadCompleteEvent,
    UploadDocumentResponse,
)
from ..common import (
    group_store,
    parse_pipeline_spec,
    require_group_member,
    require_group_write,
    resolve_llm_config,
    safe_path,
)
from ..operations import (
    build_tree_response,
    delete_directory_internal,
    delete_single,
    ensure_upload_slot,
    get_document_response,
    list_assets,
    list_documents_for_store,
    move_document_internal,
    process_collection,
    read_collection_zip,
    reconvert_single,
    reconvert_single_stream,
    update_asset_description,
    upload_file,
    upload_file_stream,
    validate_collection_upload,
)
from ..models import ReconvertRequest

__all__ = ["router"]

router = APIRouter()


@router.get("/groups/{group_id}/directories")
async def get_group_directories(
    group_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DirectoryTreeResponse:
    """Get the directory tree for a group the user belongs to."""
    safe_id = require_group_member(user, group_id)
    return build_tree_response(group_store(safe_id))


@router.get("/groups/{group_id}/documents")
async def list_group_documents(
    group_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentListResponse:
    """List all documents in a group's data directory."""
    safe_id = require_group_member(user, group_id)
    return list_documents_for_store(group_store(safe_id))


@router.get("/groups/{group_id}/documents/assets/{filepath:path}")
async def list_group_document_assets(
    group_id: str,
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetListResponse:
    """List assets for a group document."""
    safe_id = require_group_member(user, group_id)
    safe = safe_path(filepath)
    return list_assets(group_store(safe_id), safe)


@router.patch("/groups/{group_id}/documents/assets/{filepath:path}")
async def patch_group_asset_description(
    group_id: str,
    filepath: str,
    request: UpdateAssetDescriptionRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AssetEntry:
    """Update an asset's companion .md description in a group."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(filepath)
    return update_asset_description(
        group_store(safe_id), safe, request.asset_name, request.content
    )


@router.get("/groups/{group_id}/documents/{filepath:path}")
async def get_group_document_content(
    group_id: str,
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Get content of a group document or asset the user has access to."""
    safe_id = require_group_member(user, group_id)
    safe = safe_path(filepath)
    return get_document_response(group_store(safe_id), safe)


@router.put("/groups/{group_id}/documents/stream/{filepath:path}", response_class=EventSourceResponse)
async def upload_group_document_stream(
    group_id: str,
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
    overwrite: bool = Form(default=False),
) -> AsyncIterable[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
    """Upload a document to a group with streaming progress events."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(filepath)
    store = group_store(safe_id)
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


@router.put("/groups/{group_id}/documents/{filepath:path}")
async def upload_group_document(
    group_id: str,
    filepath: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
    overwrite: bool = Form(default=False),
) -> UploadDocumentResponse:
    """Upload a document to a group's knowledge base."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(filepath)
    store = group_store(safe_id)
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


@router.post("/groups/{group_id}/documents/reconvert/stream/{filepath:path}", response_class=EventSourceResponse)
async def reconvert_group_document_stream(
    group_id: str,
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
    """Re-convert a group document with streaming progress events."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(filepath)
    store = group_store(safe_id)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)
    async for event in reconvert_single_stream(store, safe, request.pipeline, resolved):
        yield event


@router.post("/groups/{group_id}/documents/collections")
async def upload_group_collection(
    group_id: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> CollectionUploadResponse:
    """Upload a ZIP collection to a group's knowledge base."""
    safe_id = require_group_write(user, group_id)
    spec, resolved = validate_collection_upload(pipeline_spec, llm_config)
    store = group_store(safe_id)
    raw = await read_collection_zip(file)

    result: CollectionCompleteEvent | None = None
    async for event in process_collection(store, raw, spec, resolved):
        if isinstance(event, CollectionCompleteEvent):
            result = event

    assert result is not None
    return result


@router.post("/groups/{group_id}/documents/collections/stream", response_class=EventSourceResponse)
async def upload_group_collection_stream(
    group_id: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> AsyncIterable[CollectionProgressEvent | CollectionCompleteEvent]:
    """Upload a collection to a group with streaming progress events."""
    safe_id = require_group_write(user, group_id)
    spec, resolved = validate_collection_upload(pipeline_spec, llm_config)
    store = group_store(safe_id)
    raw = await read_collection_zip(file)
    async for event in process_collection(store, raw, spec, resolved):
        yield event


@router.delete("/groups/{group_id}/documents/{filepath:path}")
async def delete_group_document(
    group_id: str,
    filepath: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDocumentResponse:
    """Delete a document from a group's knowledge base."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(filepath)
    store = group_store(safe_id)
    delete_single(store, safe)
    mark_dirty_and_sync(store)
    return DeleteDocumentResponse(
        filename=safe,
        message="Document deleted successfully",
    )


@router.post("/groups/{group_id}/directories")
async def create_group_directory(
    group_id: str,
    request: CreateDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateDirectoryResponse:
    """Create a new directory within a group's documents directory."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(request.path)
    store = group_store(safe_id)
    directory_path = store.workspace_dir(settings.data_dir) / safe
    if directory_path.exists():
        raise HTTPException(status_code=409, detail="Directory already exists")
    directory_path.mkdir(parents=True, exist_ok=True)
    return CreateDirectoryResponse(
        path=safe,
        message="Directory created successfully",
    )


@router.delete("/groups/{group_id}/directories")
async def delete_group_directory(
    group_id: str,
    request: DeleteDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDirectoryResponse:
    """Delete a directory from a group's documents."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(request.path)
    store = group_store(safe_id)
    files_deleted = delete_directory_internal(store, safe)
    mark_dirty_and_sync(store)
    return DeleteDirectoryResponse(
        path=safe,
        files_deleted=files_deleted,
        message="Directory deleted successfully",
    )


@router.post("/groups/{group_id}/documents/reconvert/{filepath:path}")
async def reconvert_group_document(
    group_id: str,
    filepath: str,
    request: ReconvertRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Re-convert a group document from its original binary file."""
    safe_id = require_group_write(user, group_id)
    safe = safe_path(filepath)
    store = group_store(safe_id)
    resolved = resolve_llm_config(request.llm, default_model=settings.llm.vision_model)
    result = await reconvert_single(store, safe, request.pipeline, resolved)
    mark_dirty_and_sync(store)
    return result


@router.post("/groups/{group_id}/documents/move/{filepath:path}")
async def move_group_document(
    group_id: str,
    filepath: str,
    request: MoveDocumentRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDocumentResponse:
    """Move a group document to a new location."""
    safe_id = require_group_write(user, group_id)
    src = safe_path(filepath)
    dst = safe_path(request.destination)
    if src == dst:
        raise HTTPException(
            status_code=400, detail="Source and destination are the same"
        )
    return move_document_internal(group_store(safe_id), src, dst)
