"""Routes for conversations and chat orchestration."""

import logging
from collections.abc import Sequence
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from pydantic_ai import DeferredToolRequests
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, TextPart, UserPromptPart
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import Response

from ...agents import (
    TOOLSET_GROUPS,
    UserDeps,
    base_agent,
    build_toolsets,
    user_agent,
)
from ...auth import User, get_current_user
from ...compaction import CompactionResult, compact_conversation
from ...llm import model_from_config, thinking_model_settings
from ...mcp import build_mcp_server, validate_mcp_servers
from ...db._common import new_id
from ...db.conversations import (
    ConversationSummary,
    append_branch,
    conversation_exists,
    delete_all_conversations,
    export_conversation,
    list_conversations,
    load_active_for_display,
    load_conversation,
    load_conversation_summary,
    remove_conversation,
    resolve_fork,
    set_active_leaf,
    set_conversation_title,
)
from ...db.memory import load_memory
from ...prompts import (
    CITATION_INSTRUCTIONS,
    IMAGE_INSTRUCTIONS,
    LANGUAGE_INSTRUCTIONS,
    MATH_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS_EMPTY,
    PERSONALITY_TEMPLATES,
    PLAN_INSTRUCTIONS,
    Personality,
    join_instructions,
)
from ...tools.formatting import BLOCK_SEP
from ...types import (
    BulkDeleteConversationsResponse,
    ChatRequestConfig,
    CompactConversationRequest,
    CompactConversationResponse,
    REASONING_EFFORT_VALUES,
    ConversationListResponse,
    DeleteConversationResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    LlmConfig,
    SelectBranchRequest,
    ToolsSpec,
    UpdateTitleRequest,
)
from ..cancellation import run_until_disconnect
from ..vercel import SDK_VERSION, ChatAdapter, dump_messages_with_ids, run_and_persist
from ..common import (
    group_stores,
    parse_document_filters,
    prepare_llm_config,
    user_store,
)
from ..operations import attachment_disposition

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
            if isinstance(part, (UserPromptPart, TextPart)):
                if isinstance(part.content, str) and (text := part.content.strip()):
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
        resolved = await prepare_llm_config(request.llm)
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


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteConversationResponse:
    """Delete a conversation."""
    if not await remove_conversation(user.id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return DeleteConversationResponse(
        id=conversation_id,
        message="Conversation deleted successfully",
    )


@router.delete("/conversations")
async def delete_all_conversations_route(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteConversationsResponse:
    """Delete all conversations for the authenticated user."""
    return BulkDeleteConversationsResponse(
        deleted_count=await delete_all_conversations(user.id),
        message="All conversations deleted successfully",
    )


@router.post("/conversations/{conversation_id}/compaction")
async def create_conversation_compaction(
    conversation_id: str,
    request: CompactConversationRequest,
    http_request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> CompactConversationResponse:
    """Compact a conversation by summarizing it into a new conversation.

    Summarization defaults to the regular chat model, not ``aux_model``:
    it spans the whole (typically overflowing) conversation, which needs
    the full context window rather than a small scoped-task model.
    """

    messages = ChatAdapter.load_messages(request.messages)

    async def _compact() -> CompactionResult:
        llm_config = await prepare_llm_config(request.llm, tier="main")
        return await compact_conversation(
            user.id, conversation_id, messages, llm_config
        )

    try:
        result = await run_until_disconnect(http_request, _compact())
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
    regenerate / branch-select; forking nodes carry branch metadata.
    """
    result = await load_active_for_display(user.id, conversation_id)
    if result is None:
        return []
    pairs, siblings = result
    return dump_messages_with_ids(pairs, siblings=siblings)


@router.post("/conversations/{conversation_id}/branches/select")
async def select_branch(
    conversation_id: str,
    request: SelectBranchRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> list[UIMessage]:
    """Switch the active branch to the one containing ``message_id``.

    Returns the new active path; the active leaf moves to that branch's tip.
    """
    result = await set_active_leaf(user.id, conversation_id, request.message_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation or message not found")
    pairs, siblings = result
    return dump_messages_with_ids(pairs, siblings=siblings)


@router.get("/conversations/{conversation_id}/export")
async def export_conversation_route(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Export a conversation as a downloadable JSON dump of its raw payloads.

    Read-only debugging aid: there is no import counterpart.
    """
    export = await export_conversation(user.id, conversation_id)
    if export is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(
        content=export.model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": attachment_disposition(
                f"conversation-{conversation_id}.json"
            )
        },
    )


async def _parse_chat_config(request: Request) -> ChatRequestConfig:
    """Parse chat configuration from the request body.

    Defaults and the SSRF DNS check are applied later by
    :func:`prepare_llm_config` at the request boundary.
    """
    body = await request.json()
    llm = LlmConfig(**(body.get("llm") or {}))
    try:
        personality = Personality(body.get("personality", "default"))
    except ValueError:
        personality = Personality.DEFAULT

    system_message: str = body.get("system_message") or ""
    raw_effort = body.get("reasoning_effort", "auto")
    reasoning_effort = raw_effort if raw_effort in REASONING_EFFORT_VALUES else "auto"
    included_documents: list[str] = body.get("included_documents") or []
    excluded_documents: list[str] = body.get("excluded_documents") or []
    tools = ToolsSpec(**(body.get("tools") or {}))
    raw_mode = body.get("mode", "execute")
    mode = raw_mode if raw_mode in ("plan", "execute") else "execute"
    raw_trigger = body.get("trigger")
    trigger = (
        raw_trigger
        if raw_trigger in ("submit-message", "regenerate-message")
        else "submit-message"
    )

    return ChatRequestConfig(
        conversation_id=body.get("conversation_id", ""),
        personality=personality,
        system_message=system_message,
        reasoning_effort=reasoning_effort,
        mode=mode,
        llm=llm,
        included_documents=included_documents,
        excluded_documents=excluded_documents,
        tools=tools,
        trigger=trigger,
        message_id=body.get("messageId") or None,
    )


async def _run_chat(conversation_id: str, request: Request, user: User) -> Response:
    """Stream a chat turn for *conversation_id* via the Vercel AI protocol."""
    config = await _parse_chat_config(request)
    config.conversation_id = conversation_id

    config.llm = await prepare_llm_config(config.llm, tier="main")
    try:
        await validate_mcp_servers(config.tools.mcp_servers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    document_filter, group_filters = parse_document_filters(
        config.included_documents,
        config.excluded_documents,
        user.all_groups,
    )

    if config.personality == Personality.CUSTOM and config.system_message:
        parts = [config.system_message]
    else:
        parts = [
            PERSONALITY_TEMPLATES.get(
                config.personality,
                PERSONALITY_TEMPLATES[Personality.DEFAULT],
            )
        ]

    parts.extend(
        [
            LANGUAGE_INSTRUCTIONS,
            CITATION_INSTRUCTIONS,
            IMAGE_INSTRUCTIONS,
            MATH_INSTRUCTIONS,
        ]
    )

    if config.mode == "plan":
        parts.append(PLAN_INSTRUCTIONS)

    memory_enabled = "save_memory" not in (config.tools.disabled_tools or [])
    if memory_enabled:
        memory_content = await load_memory(user.id)
        if memory_content:
            parts.append(MEMORY_INSTRUCTIONS.format(memory_content=memory_content))
        else:
            parts.append(MEMORY_INSTRUCTIONS_EMPTY)

    instructions = join_instructions(parts)

    store = user_store(user)
    user_group_stores = group_stores(user)

    # "auto" maps to None so thinking_model_settings omits the field and the
    # server-side default applies; "none" maps to False (disabled), every
    # other level passes through.  A configured max_tokens applies regardless.
    thinking = (
        None
        if config.reasoning_effort == "auto"
        else False
        if config.reasoning_effort == "none"
        else config.reasoning_effort
    )
    model_settings = thinking_model_settings(thinking, config.llm)

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

    deps = UserDeps(
        user_id=user.id,
        store=store,
        group_stores=user_group_stores,
        document_filter=document_filter,
        group_filters=group_filters,
        llm=config.llm,
    )
    toolsets = build_toolsets(
        TOOLSET_GROUPS,
        config.tools,
        extra=[
            build_mcp_server(server)  # .defer_loading()
            for server in config.tools.mcp_servers
        ],
        mode=config.mode,
    )

    # History is server-authoritative: load the active-path prefix up to the
    # fork point from the DB and replay it, ignoring the browser echo.  The
    # client sends only the new message (none for a regenerate).
    prefix, fork_id = await resolve_fork(
        user.id,
        conversation_id,
        regenerate=config.trigger == "regenerate-message",
        message_id=config.message_id,
    )

    stream = adapter.run_stream(
        deps=deps,
        output_type=[str, DeferredToolRequests],
        model=model_from_config(config.llm),
        toolsets=toolsets,
        instructions=instructions,
        model_settings=model_settings,
        message_history=prefix,
    )

    # Capture only the prefix length, not the prefix list, so the closure
    # (held for the whole stream) does not pin the full replayed history.
    prefix_len = len(prefix)

    async def persist(messages: Sequence[ModelMessage]) -> None:
        # Append only the turn's new messages (past the replayed prefix) as a
        # branch under the fork point.
        await append_branch(user.id, conversation_id, fork_id, messages[prefix_len:])

    return await run_and_persist(adapter, stream, persist=persist)


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

    CORS constraint: the frontend can only read this header same-origin or
    when the edge proxy lists it under ``Access-Control-Expose-Headers``.
    Same-origin needs no config; cross-origin deployments must expose it
    explicitly (custom response headers are hidden from JS otherwise).
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
