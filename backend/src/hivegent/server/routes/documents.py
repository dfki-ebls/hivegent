"""Routes for documents, directories, and collections in a workspace.

Every workspace reference is a canonical workspace path: ``~/<local>`` for
the caller's personal store, or ``@<group>/<local>`` for a group the caller
can access. Scope-level endpoints (collection upload, delete-all, the tree)
take the bare scope segment (``~`` or ``@<group>``), item endpoints the full
path, and ``POST /changes`` carries its paths in the body.
:func:`resolve_workspace_path` maps either to its store and enforces
group membership (reads) or write access.
"""

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import UUID

from exar.memory.mounts import strip
from exar.text import plural
from fastapi import APIRouter, Form, Header, HTTPException, UploadFile, status
from pydantic import AwareDatetime
from starlette.responses import Response

from ... import workspace
from ...auth import CurrentUser
from ...config import settings
from ...db.conversations import OwnedConversation, owned_conversation
from ...db.documents import get_document, get_line_counts
from ...jobs import JobContext, JobView, JobWork, manager
from ...shared.l10n import Localized, format_bytes
from ...skills import SKILLS_PREFIX, skill_search_paths
from ...store import User, Workspace
from ...tmp import TMP_PREFIX, tmp_dir, tmp_root
from ...tools.base import resolve_accessible_file
from ...tools.changes import WorkspaceChanged, Write
from ...tools.entries import entry_exists, is_case_base_file
from ...types import (
    AssetEntry,
    AssetListResponse,
    CollectionCompleteEvent,
    CollectionProgressEvent,
    DirectoryTreeResponse,
    DocumentChunksResponse,
    DocumentLineCountsResponse,
    GenerateAssetDescriptionRequest,
    ProgressReporter,
    UpdateAssetDescriptionRequest,
    WriteDocumentRequest,
)
from ...workspace.operations import Location, Root
from ...workspace.paths import already_exists, document_not_found, file_too_large
from ..common import (
    resolve_workspace,
    resolve_workspace_path,
)
from ..models import (
    ChangesRequest,
    CreateDirOperation,
    DeleteOperation,
    DocumentLineCountsRequest,
    MoveDestination,
    MoveOperation,
    MovePaths,
    RechunkRequest,
    ReconvertRequest,
)
from ..operations import (
    attachment_response,
    build_tree_response,
    enforce_upload_size,
    get_document_response,
    get_file_response,
    list_assets,
    run_bulk_document_job,
    spool_dir,
    summarize_failed_files,
    upload_body_limit,
)
from ..rate_limit import GENERATION_LIMIT, UPLOAD_LIMIT

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()

# Read buffer for streaming an upload to its spool file.
_SPOOL_CHUNK_SIZE = 1024 * 1024


def _collection_too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"Collection too large. Maximum size: {format_bytes(limit)}",
        de=f"Sammlung zu groß. Maximale Größe: {format_bytes(limit)}",
    )


def _documents(count: int) -> Localized[str]:
    return Localized(
        en=f"{count} {plural(count, 'document')}",
        de=f"{count} {plural(count, 'Dokument', 'Dokumente')}",
    )


def _rechunk_title(count: int) -> Localized[str]:
    return Localized(
        en=f"Rechunk {_documents(count).en}", de=f"{_documents(count).de} neu chunken"
    )


def _reconvert_title(count: int) -> Localized[str]:
    return Localized(
        en=f"Reconvert {_documents(count).en}",
        de=f"{_documents(count).de} neu konvertieren",
    )


_RECHUNKED = Localized(en="Rechunked", de="Neu gechunkt")


_RECONVERTED = Localized(en="Reconverted", de="Neu konvertiert")


def _not_imported(count: int, summary: str) -> Localized[str]:
    return Localized(
        en=f"{count} {plural(count, 'file')} not imported. {summary}",
        de=(f"{count} {plural(count, 'Datei', 'Dateien')} nicht importiert. {summary}"),
    )


_TOO_MANY_JOBS = Localized(
    en="Too many of your uploads are still waiting or running. Try again once some have finished.",
    de="Zu viele deiner Uploads warten noch oder laufen. Versuche es erneut, sobald einige fertig sind.",
)
_NO_CHUNKS = Localized(
    en="No chunks found for this document",
    de="Für dieses Dokument wurden keine Chunks gefunden",
)
_STAGE_CHUNKING = Localized(en="Chunking document", de="Dokument wird gechunkt")


@asynccontextmanager
async def _spooled(
    file: UploadFile,
    user: User,
    *,
    limit: int,
    too_large: Callable[[int], Localized[str]],
) -> AsyncGenerator[Path]:
    """Persist an upload to a temp file so a queued job needn't pin it in RAM.

    Starlette has already spooled the upload to ``file.file`` (rolling it to
    disk past its in-memory threshold); this copies it to a file we own so it
    survives past the request, which closes Starlette's temp file.  The job
    reads it back when it runs and its ``on_settled`` finalizer unlinks it, so a
    job waiting behind the concurrency limit holds only a path (not the whole
    payload), which bounds memory when many uploads are enqueued.  Spool files
    live in the transient folder emptied at boot (:func:`~hivegent.files.reset_transient`), so
    a restart that cuts a job short cannot leak them.  The user's unfinished
    jobs bound how many and how large these may be
    (:meth:`~hivegent.jobs.JobManager.reserve`), the room held from before the
    copy until the block ends, in which the caller submits its job, so
    parallel uploads cannot all pass the bound.  The file is removed when the
    block raises.
    """
    enforce_upload_size(file, limit=limit, too_large=too_large)

    if (reservation := manager.reserve(user.id, file.size or 0)) is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, _TOO_MANY_JOBS.current)

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

    try:
        spool = await asyncio.to_thread(_write)

        try:
            yield spool
        except BaseException:
            spool.unlink(missing_ok=True)
            raise
    finally:
        manager.release(reservation)


class DocumentJobKind(StrEnum):
    """Kinds of document background jobs, which keeps the ``kind`` strings out of the route bodies."""

    UPLOAD = "document.upload"
    REPLACE_ORIGINAL = "document.replace_original"
    COLLECTION = "document.collection"
    RECONVERT = "document.reconvert"
    RECHUNK = "document.rechunk"


def _submit_document_job(
    *,
    user: User,
    safe: str,
    kind: DocumentJobKind,
    work: JobWork,
    on_settled: Callable[[], None] | None = None,
    spooled: int = 0,
) -> JobView:
    """Submit a single-document job, titled by the document's filename."""
    return manager.submit(
        kind=kind,
        title=PurePosixPath(safe).name,
        owner=user,
        work=work,
        on_settled=on_settled,
        spooled=spooled,
    )


def _submit_bulk_job(
    *,
    user: User,
    kind: DocumentJobKind,
    title: Callable[[int], Localized[str]],
    files: list[str],
    process_one: Callable[[str, ProgressReporter], Awaitable[None]],
    verb: Localized[str],
) -> JobView:
    """Submit a per-file mutation over documents as a single background job.

    A lone file runs as a plain single-document job that fails with its own
    error.  Resolving the first entry doubles as an early access check.
    """
    _, safe = resolve_workspace_path(user, files[0], write=True)

    if len(files) == 1:
        return _submit_document_job(
            user=user,
            safe=safe,
            kind=kind,
            work=lambda ctx: process_one(files[0], ctx),
        )

    async def work(ctx: JobContext) -> None:
        await run_bulk_document_job(files, process_one, verb=verb, ctx=ctx)

    return manager.submit(
        kind=kind,
        title=title(len(files)).current,
        owner=user,
        work=work,
    )


@router.get("/documents/original/{filepath:path}")
async def download_original(
    filepath: str,
    user: CurrentUser,
) -> Response:
    """Download the original binary file for a document."""
    store, safe = resolve_workspace_path(user, filepath)
    original = await workspace.find_original(store, safe)
    return attachment_response(original)


@router.put(
    "/documents/original/{filepath:path}",
    dependencies=[UPLOAD_LIMIT],
    max_body_size=upload_body_limit(settings.limits.max_file_size_bytes),
)
async def replace_original(
    filepath: str,
    file: UploadFile,
    user: CurrentUser,
    modified_at: Annotated[AwareDatetime | None, Form()] = None,
) -> JobView:
    """Replace the original binary file and reconvert the document as a job.

    Like :func:`upload`, the bytes are spooled and the reconversion runs off
    the request, observable and cancellable through the ``/jobs`` endpoints.
    """
    store, safe = resolve_workspace_path(user, filepath, write=True)
    new_filename = file.filename
    async with _spooled(
        file, user, limit=settings.limits.max_file_size_bytes, too_large=file_too_large
    ) as spool:

        async def work(ctx: JobContext) -> None:
            content = await asyncio.to_thread(spool.read_bytes)
            await workspace.replace_original(
                store,
                safe,
                content,
                new_filename=new_filename,
                modified_at=modified_at,
                ctx=ctx,
            )

        return _submit_document_job(
            user=user,
            safe=safe,
            kind=DocumentJobKind.REPLACE_ORIGINAL,
            work=work,
            on_settled=lambda: spool.unlink(missing_ok=True),
            spooled=file.size or 0,
        )


@router.put(
    "/documents/{filepath:path}",
    dependencies=[UPLOAD_LIMIT],
    max_body_size=upload_body_limit(settings.limits.max_file_size_bytes),
)
async def upload_document(
    filepath: str,
    file: UploadFile,
    user: CurrentUser,
    modified_at: Annotated[AwareDatetime | None, Form()] = None,
    if_none_match: Annotated[
        str | None,
        Header(description="`*` creates the document only, refused while one exists"),
    ] = None,
) -> JobView:
    """Create or replace a document and process it as a background job.

    Returns the job's initial snapshot as soon as the bytes are received;
    conversion and indexing then run off the request, observable and
    cancellable through the ``/jobs`` endpoints, so the UI stays usable and
    closing the tab no longer aborts the work.  Sent again, it lands the same
    document, unless ``If-None-Match: *`` asks for a new one, which a document
    at the path refuses with ``412``.
    """
    store, safe = resolve_workspace_path(user, filepath, write=True)
    create_only = if_none_match == "*"

    if create_only and await asyncio.to_thread(entry_exists, store.path, safe):
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED, already_exists(filepath).current
        )

    async with _spooled(
        file, user, limit=settings.limits.max_file_size_bytes, too_large=file_too_large
    ) as spool:

        async def work(ctx: JobContext) -> None:
            content = await asyncio.to_thread(spool.read_bytes)
            await workspace.upload(
                store,
                safe,
                content,
                modified_at=modified_at,
                overwrite=not create_only,
                ctx=ctx,
            )

        return _submit_document_job(
            user=user,
            safe=safe,
            kind=DocumentJobKind.UPLOAD,
            work=work,
            on_settled=lambda: spool.unlink(missing_ok=True),
            spooled=file.size or 0,
        )


@router.post(
    "/documents/collections/{target:path}",
    dependencies=[UPLOAD_LIMIT],
    max_body_size=upload_body_limit(settings.limits.max_collection_size_bytes),
)
async def upload_collection(
    target: str,
    file: UploadFile,
    user: CurrentUser,
) -> JobView:
    """Accept a ZIP collection and process it as a background job.

    Each file flows through its own upload, so the job reports per-file
    progress and the workspace stays usable while a large archive processes.
    ``target`` is a canonical directory: a scope root (``~``, ``@<group>``) or a
    subdir under it, so a drop can target a folder, not just the workspace root;
    it resolves to the store and destination subdir like every other item route.
    """
    store, dest = resolve_workspace_path(user, target, write=True)
    async with _spooled(
        file,
        user,
        limit=settings.limits.max_collection_size_bytes,
        too_large=_collection_too_large,
    ) as spool:
        # Enforce every collection limit here, before the job is queued, so a
        # too-large, too-many-files, or malformed archive is rejected synchronously
        # with a clear reason instead of failing the job much later.
        await asyncio.to_thread(workspace.validate_collection_archive, spool)

        async def work(ctx: JobContext) -> None:
            # The ZIP is read straight from the spool file, so a large archive is
            # never loaded into memory whole.
            async for event in workspace.process_collection(
                store, spool, dest_dir=dest
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
                            _not_imported(
                                len(event.failed_files),
                                summarize_failed_files(event.failed_files),
                            ).current
                        )

        return manager.submit(
            kind=DocumentJobKind.COLLECTION,
            title=file.filename or "collection.zip",
            owner=user,
            work=work,
            on_settled=lambda: spool.unlink(missing_ok=True),
            spooled=file.size or 0,
        )


@router.delete(
    "/workspaces/{scope:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[UPLOAD_LIMIT],
)
async def delete_all_documents(
    scope: str,
    user: CurrentUser,
) -> None:
    """Delete all documents, chunks, and originals in a workspace."""
    await workspace.delete_all(resolve_workspace(user, scope, write=True))


@router.post("/documents/rechunk", dependencies=[UPLOAD_LIMIT])
async def rechunk_documents(
    request: RechunkRequest,
    user: CurrentUser,
) -> JobView:
    """Rechunk documents with the operator's pipeline as a single background job."""

    async def _rechunk_one(filepath: str, ctx: ProgressReporter) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        ctx.set_stage(_STAGE_CHUNKING.current)
        await workspace.rechunk(store, safe)

    return _submit_bulk_job(
        user=user,
        kind=DocumentJobKind.RECHUNK,
        title=_rechunk_title,
        files=request.files,
        process_one=_rechunk_one,
        verb=_RECHUNKED,
    )


@router.post("/documents/reconvert", dependencies=[UPLOAD_LIMIT])
async def reconvert_documents(
    request: ReconvertRequest,
    user: CurrentUser,
) -> JobView:
    """Reconvert documents from their originals with the operator's pipelines as a single background job."""

    async def _reconvert_one(filepath: str, ctx: ProgressReporter) -> None:
        store, safe = resolve_workspace_path(user, filepath, write=True)
        await workspace.reconvert(store, safe, ctx=ctx)

    return _submit_bulk_job(
        user=user,
        kind=DocumentJobKind.RECONVERT,
        title=_reconvert_title,
        files=request.files,
        process_one=_reconvert_one,
        verb=_RECONVERTED,
    )


@router.post("/documents/line-counts")
async def get_document_line_counts(
    request: DocumentLineCountsRequest,
    user: CurrentUser,
) -> DocumentLineCountsResponse:
    """Batch-resolve document line counts for the context-panel coverage map.

    Each requested path is resolved to its store (skipping web URLs and any the
    caller cannot reach), grouped per store, and looked up in one query each.
    Unknown or not-yet-indexed paths are simply omitted from the response.
    """
    grouped: dict[Workspace, dict[str, str]] = {}
    for original in request.files:
        try:
            store, safe = resolve_workspace_path(user, original)
        except HTTPException:
            continue
        if not safe:
            continue
        grouped.setdefault(store, {})[safe] = original

    counted = await asyncio.gather(
        *(
            get_line_counts(store, list(safe_to_original))
            for store, safe_to_original in grouped.items()
        )
    )
    line_counts = {
        safe_to_original[safe]: count
        for safe_to_original, counts in zip(grouped.values(), counted, strict=True)
        for safe, count in counts.items()
    }

    return DocumentLineCountsResponse(line_counts=line_counts)


@router.get("/documents/chunks/{filepath:path}")
async def get_document_chunks(
    filepath: str,
    user: CurrentUser,
) -> DocumentChunksResponse:
    """Get chunks for a document."""
    store, safe = resolve_workspace_path(user, filepath)
    chunked = await get_document(store, safe)
    if not chunked:
        raise HTTPException(status_code=404, detail=_NO_CHUNKS.current)

    original = chunked.original_path

    return DocumentChunksResponse(
        **dict(chunked),
        case_base_candidate=original is not None and is_case_base_file(original),
    )


@router.get("/documents/assets/{filepath:path}")
async def list_document_assets(
    filepath: str,
    user: CurrentUser,
) -> AssetListResponse:
    """List assets for a document."""
    store, safe = resolve_workspace_path(user, filepath)
    return await asyncio.to_thread(list_assets, store, safe)


@router.patch("/documents/assets/{filepath:path}")
async def patch_asset_description(
    filepath: str,
    request: UpdateAssetDescriptionRequest,
    user: CurrentUser,
) -> AssetEntry:
    """Update an asset's companion .md description."""
    store, safe = resolve_workspace_path(user, filepath, write=True)

    return await workspace.update_asset_description(
        store,
        safe,
        request.asset_name,
        request.content,
    )


@router.post("/documents/assets/{filepath:path}", dependencies=[GENERATION_LIMIT])
async def generate_asset_description(
    filepath: str,
    request: GenerateAssetDescriptionRequest,
    user: CurrentUser,
) -> AssetEntry:
    """Generate an asset's companion .md description with the vision model."""
    store, safe = resolve_workspace_path(user, filepath, write=True)

    return await workspace.generate_asset_description(store, safe, request.asset_name)


@router.delete("/documents/assets/{filepath:path}")
async def delete_asset_description(
    filepath: str,
    asset_name: str,
    user: CurrentUser,
) -> AssetEntry:
    """Delete an asset's companion .md description, keeping the asset itself."""
    store, safe = resolve_workspace_path(user, filepath, write=True)

    return await workspace.delete_asset_description(store, safe, asset_name)


# Registered after the more specific /documents/assets/ PATCH route so the
# catch-all path parameter cannot shadow it.
@router.patch(
    "/documents/{filepath:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[UPLOAD_LIMIT],
)
async def write_document(
    filepath: str,
    request: WriteDocumentRequest,
    user: CurrentUser,
) -> None:
    """Replace a text document's content in place.

    Unlike the PUT upload route this keeps the entry's original binary,
    assets, and provenance, and only rewrites the markdown and its chunks.
    """
    store, safe = resolve_workspace_path(user, filepath, write=True)
    write = Write(
        Location(Root.of(store), safe),
        request.content,
        mode=request.mode,
    )
    await workspace.apply_changeset((write,))


def _tmp_file(conversation: OwnedConversation, filepath: str) -> Path | None:
    """The file *filepath* names in the ``/tmp`` of *conversation*, as the agent's tools resolve it."""
    root = tmp_root(tmp_dir(settings.data_dir, conversation))
    resolved = resolve_accessible_file((root.search_path,), filepath)

    return None if resolved is None else resolved[2]


def _skill_file(user: User, filepath: str) -> Path | None:
    """The file *filepath* names among the skills *user* sees, as the agent's tools resolve it."""
    resolved = resolve_accessible_file(skill_search_paths(user), filepath)

    return None if resolved is None else resolved[2]


@router.get("/documents/{filepath:path}")
async def get_document_content(
    filepath: str,
    user: CurrentUser,
    conversation_id: UUID | None = None,
) -> Response:
    """Get the content of a document, an asset, or a file of a skill.

    A ``/tmp`` path is read from the folder of *conversation_id*, which the
    user must own, and a ``/skills`` path from the skills the user sees.
    """
    if strip(SKILLS_PREFIX, filepath) is not None:
        return await get_file_response(lambda: _skill_file(user, filepath), filepath)

    if conversation_id is None or strip(TMP_PREFIX, filepath) is None:
        store, safe = resolve_workspace_path(user, filepath)

        return await get_document_response(store, safe)

    conversation = await owned_conversation(user, conversation_id)

    if conversation is None:
        raise HTTPException(
            status_code=404, detail=document_not_found(filepath).current
        )

    return await get_file_response(lambda: _tmp_file(conversation, filepath), filepath)


@router.get("/directories/{scope:path}")
async def get_directories(
    scope: str,
    user: CurrentUser,
) -> DirectoryTreeResponse:
    """Build a recursive directory tree for a workspace by its scope root."""
    return await build_tree_response(resolve_workspace(user, scope))


async def _apply(
    user: User,
    operations: Iterable[MoveOperation | DeleteOperation | CreateDirOperation],
) -> WorkspaceChanged:
    """Resolve every path with write access and apply *operations* as one changeset, which the feed announces."""

    def at(path: str) -> Location:
        store, safe = resolve_workspace_path(user, path, write=True)

        return Location(Root.of(store), safe)

    items = tuple(op.operation(at) for op in operations)

    return (await workspace.apply_changeset(items)).changed


@router.post("/changes", dependencies=[UPLOAD_LIMIT])
async def apply_changes(
    request: ChangesRequest,
    user: CurrentUser,
) -> WorkspaceChanged:
    """Move, delete, and create documents and directories as one changeset.

    Every path is resolved with write access, so a move between workspaces is
    allowed exactly when the caller may write both ends.  The operations land
    at once, all of them or none, so swaps and chains work, and one refused
    operation rejects the batch with the gateway's message naming its path.
    The single-item routes below are shortcuts for one operation of this kind.
    Every route answers what changed, which the feed announces under the
    same id, so the caller applies it at once and skips the announcement.
    """
    return await _apply(user, request.operations)


@router.post("/documents/move/{filepath:path}", dependencies=[UPLOAD_LIMIT])
async def move_document(
    filepath: str,
    request: MoveDestination,
    user: CurrentUser,
) -> WorkspaceChanged:
    """Move or rename one document, a shortcut for one move in ``POST /changes``."""
    move = MoveOperation(kind="move", source=filepath, destination=request.destination)
    return await _apply(user, [move])


@router.delete("/documents/{filepath:path}", dependencies=[UPLOAD_LIMIT])
async def delete_document(
    filepath: str,
    user: CurrentUser,
) -> WorkspaceChanged:
    """Delete one document, a shortcut for one entry delete in ``POST /changes``."""
    delete = DeleteOperation(kind="delete", path=filepath, expect="entry")
    return await _apply(user, [delete])


@router.put("/directories/{path:path}", dependencies=[UPLOAD_LIMIT])
async def create_directory(
    path: str,
    user: CurrentUser,
) -> WorkspaceChanged:
    """Create one directory, a shortcut for one mkdir in ``POST /changes``.

    One that exists already is what was asked for, so nothing changes.
    """
    store, safe = resolve_workspace_path(user, path, write=True)

    if await asyncio.to_thread(Location(Root.of(store), safe).full_path.is_dir):
        return WorkspaceChanged(())

    return await _apply(user, [CreateDirOperation(kind="mkdir", path=path)])


@router.post("/directories/move", dependencies=[UPLOAD_LIMIT])
async def move_directory(
    request: MovePaths,
    user: CurrentUser,
) -> WorkspaceChanged:
    """Move or rename one directory, a shortcut for one move in ``POST /changes``."""
    move = MoveOperation(
        kind="move", source=request.source, destination=request.destination
    )
    return await _apply(user, [move])


@router.delete("/directories/{path:path}", dependencies=[UPLOAD_LIMIT])
async def delete_directory(
    path: str,
    user: CurrentUser,
) -> WorkspaceChanged:
    """Delete one directory with everything in it.

    A shortcut for one directory delete in ``POST /changes``.
    """
    delete = DeleteOperation(kind="delete", path=path, expect="dir")
    return await _apply(user, [delete])
