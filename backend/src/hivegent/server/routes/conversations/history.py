"""Routes reading, renaming, deleting, exporting, and replacing the stored conversations."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.responses import Response, StreamingResponse

from .... import conversation_files, workspace
from ....auth import CurrentUser
from ....config import settings
from ....config.integrations import RagoldSettings
from ....db.conversations import (
    ConversationSummary,
    OwnedConversation,
    list_conversations,
    load_conversation,
    load_conversation_summary,
    set_conversation_title,
)
from ....ragold import RagoldRequest, build_archive
from ....runs import Run, runs
from ....shared.l10n import Localized, current_language
from ....types import (
    ConversationArchive,
    ConversationListResponse,
    UpdateTitleRequest,
)
from ...common import CONVERSATION_NOT_FOUND, found, resolve_workspace_path
from ...export import conversation_archive
from ...operations import attachment_disposition, zip_response
from ...rate_limit import GENERATION_LIMIT, UPLOAD_LIMIT
from ...vercel import dump_transcript, load_archived
from .attachments import accept_imported
from .deps import TURN_RUNNING, Opened, Owned

__all__ = ["active_messages", "router"]

router = APIRouter()


_RAGOLD_DISABLED = Localized(
    en="RAGold export is disabled", de="Der RAGold-Export ist deaktiviert"
)


def _require_ragold() -> RagoldSettings:
    """Hide the RAGold transfer while it is not configured."""
    if settings.ragold.url is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _RAGOLD_DISABLED.current)

    return settings.ragold


type RagoldConfigured = Annotated[RagoldSettings, Depends(_require_ragold)]


@router.get("/conversations")
async def get_conversations(
    user: CurrentUser,
) -> ConversationListResponse:
    """List all conversations with summary information, those whose first turn runs included."""
    return ConversationListResponse(
        conversations=await list_conversations(user, running=runs.running(user))
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation: Owned) -> ConversationSummary:
    """Get summary information for a specific conversation."""
    return found(await load_conversation_summary(conversation))


@router.patch("/conversations/{conversation_id}")
async def update_conversation_title(
    conversation: Owned, request: UpdateTitleRequest
) -> ConversationSummary:
    """Update the title of a conversation."""
    return found(await set_conversation_title(conversation, request.title))


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(conversation: Owned) -> None:
    """Delete a conversation and its files on disk, once its turn stopped."""
    if not await conversation_files.delete_conversation(conversation):
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND.current)


@router.delete("/conversations", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_conversations_route(
    user: CurrentUser,
) -> None:
    """Delete all conversations for the authenticated user, and their files on disk."""
    await conversation_files.delete_conversations(user)


async def active_messages(
    conversation: OwnedConversation, run: Run | None = None
) -> list[UIMessage]:
    """The transcript of *conversation* as the client addresses it.

    While *run* streams, it is the history the run replays, with the client
    message it answers or continues in its place, so a client following the
    run again sees what it answers, and a continuation's replay lands in the
    assistant message it continues, its decisions included.
    """
    if run is not None and await run.prepared():
        messages = dump_transcript(run.history, None)

        if run.message is None:
            return messages

        return [*(m for m in messages if m.id != run.message.id), run.message]

    loaded = await load_conversation(conversation)

    return (
        []
        if loaded is None
        else dump_transcript(loaded.messages, loaded.deferred_tool_requests)
    )


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(opened: Opened) -> list[UIMessage]:
    """Get the active history of a conversation in Vercel AI format.

    Each ``UIMessage.id`` is the id the client addresses for edit and
    regenerate.  An absent id has no messages yet, so a client opens a new
    chat at the id it chose.  A running turn shows as it is followed
    (:func:`active_messages`).
    """
    conversation, exists = opened
    run = runs.get(conversation)

    return await active_messages(conversation, run) if exists or run is not None else []


@router.get("/conversations/{conversation_id}/archive", dependencies=[GENERATION_LIMIT])
async def get_conversation_archive(conversation: Owned) -> Response:
    """Export a conversation's active history as a downloadable archive.

    The payload is a :class:`ConversationArchive` whose ``backend`` half carries
    the persisted messages and the system prompts they ran under; ``frontend``
    is left unset, since this route has no browser state to speak for.  The
    export button fills that half in and downloads the same shape, and the
    counterpart :func:`put_conversation_archive` restores either one.
    """
    archive = found(await conversation_archive(conversation))

    return Response(
        content=archive.model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": attachment_disposition(
                f"conversation-{conversation.id}.json"
            )
        },
    )


@router.post("/conversations/{conversation_id}/ragold")
async def export_ragold_route(
    conversation: Owned,
    user: CurrentUser,
    request: RagoldRequest,
    ragold: RagoldConfigured,
) -> StreamingResponse:
    """Build one RAGold gold entry with its original documents as a zip.

    Every document resolves with the caller's access and the zip is complete
    before the first byte is sent, so a failure is an HTTP error, never a
    truncated archive.
    """
    paths = list(dict.fromkeys(request.documents))
    # Every scope is checked before any document is read.
    located = [resolve_workspace_path(user, path) for path in paths]
    sources = await asyncio.gather(
        *(workspace.find_source(store, safe) for store, safe in located)
    )
    files = dict(zip(paths, sources, strict=True))

    spool = await asyncio.to_thread(
        build_archive,
        request,
        files,
        author=user.name or user.id,
        project=ragold.project,
        language=current_language(),
    )

    return zip_response(spool, "ragold.zip")


@router.put("/conversations/{conversation_id}/archive", dependencies=[UPLOAD_LIMIT])
async def put_conversation_archive(
    opened: Opened, archive: ConversationArchive
) -> ConversationSummary:
    """Restore an exported archive as the conversation at a fresh id the client chose.

    A conversation the caller holds at the id is replaced, so the request lands
    the same conversation however often it is sent.

    The persisted ``backend`` half is restored when it has messages, falling
    back to ``frontend`` so an archive taken of a draft, or of a turn that
    errored before reaching the database, still imports.  The UI messages are
    decoded to model messages that keep their UI ids and stored as a single
    linear history.  The archive's system prompts are a record of the original
    run and are not replayed, since the imported conversation runs under the
    importing user's own settings.
    """
    conversation, _ = opened
    ui_messages, title = archive.active_path()

    # Its turn still writes to the history this would replace.
    if runs.get(conversation) is not None:
        raise HTTPException(status_code=409, detail=TURN_RUNNING.current)

    try:
        messages = load_archived(ui_messages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await accept_imported(messages)

    try:
        return found(
            await conversation_files.replace_conversation(
                conversation, messages, title=title
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
