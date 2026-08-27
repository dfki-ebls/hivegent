"""Routes for conversations and chat orchestration."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from pydantic_ai import BinaryContent, DeferredToolRequests
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    TextPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import Response

from ...agents import (
    UserDeps,
    base_agent,
    turn_usage_limits,
    user_agent,
)
from ...agents.subagent_events import SubagentUpdate
from ...auth import User, get_current_user
from ...compaction import compact_conversation
from ...config import settings
from ...converters import INGESTIBLE_IMAGE_MEDIA_TYPES
from ...converters.images import sanitize_image_bytes
from ...db._common import new_id
from ...db.conversations import (
    ConversationSummary,
    MessagePair,
    append_branch,
    conversation_exists,
    delete_all_conversations,
    import_conversation,
    is_user_request,
    list_conversations,
    load_active_for_display,
    load_conversation,
    load_conversation_summary,
    remove_conversation,
    resolve_fork,
    set_conversation_title,
)
from ...humanize import format_bytes
from ...llm import (
    model_from_config,
    resolve_thinking,
    thinking_model_settings,
)
from ...tools.formatting import BLOCK_SEP
from ...types import (
    AgentRunConfig,
    ChatRequestConfig,
    CompactConversationResponse,
    ConversationArchive,
    ConversationListResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    InstructionsSnapshot,
    ServerConversation,
    UpdateTitleRequest,
)
from ..cancellation import run_until_disconnect
from ..common import (
    build_run_prefix,
    prepare_llm_config,
)
from ..operations import attachment_disposition
from ..vercel import (
    SDK_VERSION,
    ChatAdapter,
    decline_pending_approvals,
    dump_messages_with_ids,
    run_and_persist,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/conversations")
async def get_conversations(
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationListResponse:
    """List all conversations with summary information."""
    conversations = await list_conversations(user.id)
    return ConversationListResponse(
        conversations=conversations,
        total_count=len(conversations),
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Get summary information for a specific conversation."""
    summary = await load_conversation_summary(user.id, conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return summary


@router.patch("/conversations/{conversation_id}")
async def update_conversation_title(
    conversation_id: str,
    request: UpdateTitleRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Update the title of a conversation."""
    summary = await set_conversation_title(user.id, conversation_id, request.title)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return summary


def _extract_message_texts(
    messages: Sequence[ModelMessage],
    max_messages: int = 4,
) -> list[str]:
    """Extract text content from conversation messages."""
    texts: list[str] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, (UserPromptPart, TextPart))
                and isinstance(part.content, str)
                and (text := part.content.strip())
            ):
                texts.append(text[:500])
                if len(texts) >= max_messages:
                    return texts
    return texts


@router.post("/conversations/{conversation_id}/title/generation")
async def generate_conversation_title(
    conversation_id: str,
    request: GenerateTitleRequest,
    http_request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> GenerateTitleResponse:
    """Generate a title for a conversation using an LLM."""
    conversation = await load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages_text = _extract_message_texts(conversation.messages)
    if not messages_text:
        return GenerateTitleResponse(title=conversation.title or "Untitled")

    conversation_preview = BLOCK_SEP.join(messages_text)
    instructions = """Generate a short, descriptive title (max 60 characters) for the conversation.
The title should capture the main topic or question.
Return ONLY the title, no quotes or extra text.
"""

    async def _generate() -> str:
        resolved = prepare_llm_config(request.llm)
        result = await base_agent.run(
            f"Conversation:\n{conversation_preview}",
            model=model_from_config(resolved),
            model_settings=thinking_model_settings(False, resolved),
            instructions=instructions,
        )
        return result.output

    try:
        output = await run_until_disconnect(http_request, _generate())
        generated_title = output.strip().strip("\"'")
        if len(generated_title) > 100:
            generated_title = generated_title[:97] + "..."

        await set_conversation_title(user.id, conversation_id, generated_title)
        return GenerateTitleResponse(title=generated_title)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to generate title for conversation %s", conversation_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to generate title",
        ) from exc


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete a conversation."""
    if not await remove_conversation(user.id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.delete("/conversations", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_conversations_route(
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete all conversations for the authenticated user."""
    await delete_all_conversations(user.id)


@router.post("/conversations/{conversation_id}/compaction")
async def create_conversation_compaction(
    conversation_id: str,
    request: AgentRunConfig,
    http_request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> CompactConversationResponse:
    """Compact a conversation by summarizing it into a new conversation.

    The body carries the run configuration only: the messages are the
    conversation's own persisted active path, the same history a chat turn
    replays.  Summarization defaults to the regular chat model, not
    ``aux_model``: it spans the whole (typically overflowing) conversation,
    which needs the full context window rather than a small scoped-task model.
    """

    run_prefix = build_run_prefix(request, user)

    try:
        result = await run_until_disconnect(
            http_request,
            compact_conversation(user.id, conversation_id, run_prefix),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelHTTPError as exc:
        logger.exception("Failed to compact conversation %s", conversation_id)
        raise HTTPException(
            status_code=502,
            detail=(
                "The summarization model rejected the request "
                f"(HTTP {exc.status_code})."
            ),
        ) from exc
    except Exception as exc:
        logger.exception("Failed to compact conversation %s", conversation_id)
        raise HTTPException(
            status_code=500,
            detail="Failed to compact conversation",
        ) from exc

    return CompactConversationResponse(
        new_conversation_id=result.new_conversation_id,
        summary=result.summary,
        message="Conversation compacted successfully",
    )


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> list[UIMessage]:
    """Get the active path of a conversation in Vercel AI format.

    Each ``UIMessage.id`` is the tree-node id the client addresses for edit /
    regenerate; forking nodes carry branch metadata.
    """
    result = await load_active_for_display(user.id, conversation_id)
    if result is None:
        return []
    pairs, siblings = result
    return dump_messages_with_ids(pairs, siblings=siblings)


def _instruction_snapshots(pairs: Sequence[MessagePair]) -> list[InstructionsSnapshot]:
    """Group *pairs* by the system prompt each message was sent under.

    pydantic-ai stamps the fully composed prompt (agent-level blocks plus every
    live capability's, including the dynamic document scope) onto each
    ``ModelRequest`` it sends, and that survives into the stored history, so the
    prompts are read back here rather than recomposed — recomposing would
    describe today's settings, not the ones the turn actually ran under.
    Consecutive messages sharing a prompt collapse into one snapshot.
    """
    snapshots: list[InstructionsSnapshot] = []

    for node_id, message in pairs:
        if not isinstance(message, ModelRequest) or not message.instructions:
            continue

        text = message.instructions

        if snapshots and snapshots[-1].text == text:
            snapshots[-1].message_ids.append(node_id)
        else:
            snapshots.append(InstructionsSnapshot(message_ids=[node_id], text=text))

    return snapshots


@router.get("/conversations/{conversation_id}/export")
async def export_conversation_route(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Export a conversation's active path as a downloadable archive.

    The payload is a :class:`ConversationArchive` whose ``backend`` half carries
    the persisted messages and the system prompts they ran under; ``frontend``
    is left unset, since this route has no browser state to speak for.  The
    export button fills that half in and downloads the same shape, and the
    counterpart :func:`import_conversation_route` restores either one.
    """
    summary = await load_conversation_summary(user.id, conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await load_active_for_display(user.id, conversation_id)
    pairs, siblings = result if result else ([], {})
    archive = ConversationArchive(
        backend=ServerConversation(
            id=summary.id,
            title=summary.title,
            messages=dump_messages_with_ids(pairs, siblings=siblings),
            instructions=_instruction_snapshots(pairs),
        )
    )
    return Response(
        content=archive.model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": attachment_disposition(
                f"conversation-{conversation_id}.json"
            )
        },
    )


@router.post("/conversations/import")
async def import_conversation_route(
    archive: ConversationArchive,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Restore an exported archive as a new conversation owned by the user.

    The persisted ``backend`` half is restored when it has messages, falling
    back to ``frontend`` so an archive taken of a draft, or of a turn that
    errored before reaching the database, still imports.  The UI messages are
    decoded to model messages and stored under fresh ids as a single linear
    branch; the archive's system prompts are a record of the original run and
    are not replayed, since the imported conversation runs under the importing
    user's own settings.  Documents referenced by embedded tool outputs that do
    not exist for this user are left unresolved rather than raising, so a
    conversation captured against a different document collection still imports
    cleanly.
    """
    ui_messages, title = archive.active_path()
    try:
        messages = ChatAdapter.load_messages(ui_messages)
        return await import_conversation(user.id, messages, title=title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _accept_attachment(item: UserContent) -> None:
    """Admit one attached item, sanitising it in place, or reject it.

    Raises :class:`HTTPException` for anything but an image within the
    size cap.  A PNG is stripped of the ancillary chunks that trip
    Pillow-based inference servers, the same best-effort pass
    ``read_binary_document`` applies to an image read off disk.
    """
    if isinstance(item, str):
        return

    if (
        not isinstance(item, BinaryContent)
        or item.media_type not in INGESTIBLE_IMAGE_MEDIA_TYPES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only images can be attached to a chat message. Upload other "
                "documents to your workspace, where they are converted and "
                "indexed for the assistant to search."
            ),
        )

    limit = settings.limits.max_attachment_bytes
    if len(item.data) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"Attached image exceeds the {format_bytes(limit)} limit.",
        )

    item.data = sanitize_image_bytes(item.data, item.media_type)


def _accept_attachments(messages: Sequence[ModelMessage]) -> None:
    """Admit the attachments of ``ChatAdapter.messages``, rejecting non-images.

    Takes the client's new prompt alone, never the replayed prefix, so an
    already-admitted image is never re-scanned on a later turn.  Anything
    but an image belongs in a workspace instead (see ``AGENTS.md``).
    """
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue

        for part in message.parts:
            if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                for item in part.content:
                    _accept_attachment(item)


async def _run_chat(conversation_id: str, request: Request, user: User) -> Response:
    """Stream a chat turn for *conversation_id* via the Vercel AI protocol.

    A turn that carries a new user message returns its tree-node id in the
    ``X-Message-Id`` response header, under the same CORS constraint as
    ``X-Conversation-Id``.
    """
    # The body is the AI SDK's, with the run configuration spliced in beside
    # its messages; extras are ignored, so one model validates both this and
    # the compaction request that has to reproduce the same prefix.
    config = ChatRequestConfig.model_validate(await request.json())

    run_prefix = build_run_prefix(config, user)
    thinking = resolve_thinking(config.reasoning_effort)
    model_settings = thinking_model_settings(thinking, run_prefix.llm)

    try:
        # `from_request` is typed to return the base adapter with erased type
        # parameters; the runtime object is a `ChatAdapter[UserDeps, str]`.
        adapter = cast(
            ChatAdapter[UserDeps, str],
            await ChatAdapter[UserDeps, str].from_request(
                request, agent=user_agent, sdk_version=SDK_VERSION
            ),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid chat request") from exc

    # Gate the new user message's attachments before the run. Sanitisation
    # lands in place, and the adapter caches `messages`, so `run_stream` and
    # persistence both see the admitted bytes.
    _accept_attachments(adapter.messages)

    # Live subagent transcript snapshots flow through this sink; the streaming
    # response drains it concurrently with the run (see `run_and_persist`).
    subagent_sink: asyncio.Queue[SubagentUpdate] = asyncio.Queue()
    deps = replace(run_prefix.deps, subagent_sink=subagent_sink)

    # History is server-authoritative: load the active-path prefix up to the
    # fork point from the DB and replay it, ignoring the browser echo.  The
    # client sends only the new message (none for a regenerate).
    prefix, fork_id = await resolve_fork(
        user.id,
        conversation_id,
        regenerate=config.trigger == "regenerate-message",
        message_id=config.message_id,
    )

    # A turn that ended awaiting approval left its call dangling so this request
    # could answer it.  One that carries anything else abandons that approval,
    # and the dangling call has to be closed here or every later turn replays it
    # as a generic "interrupted" result and the model reissues the same call
    # again and again.  Stored as its own node, so the delta below still starts
    # with the message whose id is announced.
    if adapter.deferred_tool_results is None:
        declined = decline_pending_approvals(prefix)
        if declined is not None:
            fork_id = await append_branch(user.id, conversation_id, fork_id, [declined])
            prefix = [*prefix, declined]

    # Reserve the node id the new user message will be persisted under, so the
    # client can address it for an edit without waiting for a reload.  Only a
    # request carrying a user prompt reserves one: a regenerate and a
    # post-approval continuation start their delta with a message the client
    # never addresses.
    user_node_id = (
        new_id() if adapter.messages and is_user_request(adapter.messages[-1]) else None
    )

    stream = adapter.run_stream(
        deps=deps,
        output_type=[str, DeferredToolRequests],
        model=run_prefix.model,
        capabilities=run_prefix.capabilities,
        instructions=run_prefix.instructions,
        model_settings=model_settings,
        message_history=prefix,
        usage_limits=turn_usage_limits,
    )

    # Capture only the prefix length, not the prefix list, so the closure
    # (held for the whole stream) does not pin the full replayed history.
    prefix_len = len(prefix)

    async def persist(messages: Sequence[ModelMessage]) -> None:
        # Append only the turn's new messages (past the replayed prefix) as a
        # branch under the fork point.  The delta's head is the user message
        # whose id was announced, so it lands under exactly that id.
        await append_branch(
            user.id,
            conversation_id,
            fork_id,
            messages[prefix_len:],
            head_id=user_node_id,
        )

    response = await run_and_persist(
        adapter, stream, persist=persist, subagent_sink=subagent_sink
    )
    if user_node_id is not None:
        response.headers["X-Message-Id"] = user_node_id
    return response


@router.post("/conversations/chat")
async def create_new_conversation_chat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Start a new conversation: mint a server ID and stream the first turn.

    The conversation row is created lazily when the turn is persisted (see
    :func:`append_branch`), so an abandoned chat never leaves an empty
    row behind.  Because the turn is persisted on an errored finish too,
    a first turn that errors still becomes a real row under the minted ID —
    which the client adopts so its retry targets that conversation instead
    of minting a duplicate.  The minted ID is returned in the
    ``X-Conversation-Id`` response header for the client to adopt.

    CORS constraint: the frontend can only read this header (and the
    ``X-Message-Id`` every turn returns) same-origin or when the edge proxy
    lists them under ``Access-Control-Expose-Headers``.  Same-origin needs no
    config; cross-origin deployments must expose them explicitly (custom
    response headers are hidden from JS otherwise).
    """
    conversation_id = new_id()
    response = await _run_chat(conversation_id, request, user)
    response.headers["X-Conversation-Id"] = conversation_id
    return response


@router.post("/conversations/{conversation_id}/chat")
async def create_conversation_chat(
    conversation_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Continue an existing conversation.

    Only conversations that already hold a message exist as rows, so an ID
    the server never issued is a 404 rather than a silently created
    conversation — clients cannot mint their own IDs.
    """
    if not await conversation_exists(user.id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await _run_chat(conversation_id, request, user)
