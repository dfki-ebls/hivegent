"""Routes for conversations and chat orchestration."""

import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from nanoid import generate
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelMessage, TextPart, UserPromptPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
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
from ...compaction import compact_conversation
from ...config import settings
from ...llm import create_openai_chat_model
from ...mcp import build_mcp_server, validate_mcp_servers
from ...db.conversations import (
    ConversationSummary,
    append_messages,
    delete_all_conversations,
    list_conversations,
    load_conversation,
    load_messages,
    remove_conversation,
    set_conversation_title,
)
from ...db.memory import load_memory
from ...prompts import (
    CITATION_INSTRUCTIONS,
    IMAGE_INSTRUCTIONS,
    MATH_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS_EMPTY,
    PERSONALITY_TEMPLATES,
    PLAN_INSTRUCTIONS,
    Personality,
    join_instructions,
)
from ...types import (
    BulkDeleteConversationsResponse,
    ChatRequestConfig,
    CompactConversationRequest,
    CompactConversationResponse,
    ConversationListResponse,
    CreateConversationResponse,
    DeleteConversationResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    LlmConfig,
    ToolsSpec,
    UpdateTitleRequest,
)
from ..common import (
    group_stores,
    parse_document_filters,
    prepare_llm_config,
    user_store,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/conversations")
async def create_conversation(
    _user: Annotated[User, Depends(get_current_user)],
) -> CreateConversationResponse:
    """Issue a server-generated conversation ID.

    No file is written until the first message is saved; until then the
    ID is just a reservation that the client can navigate to.  The auth
    dependency is required so anonymous clients cannot mint IDs.
    """
    return CreateConversationResponse(id=generate())


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
    conversation = await load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(conversation.messages),
        compacted_from=conversation.compacted_from,
    )


@router.put("/conversations/{conversation_id}/title")
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
    user: Annotated[User, Depends(get_current_user)],
) -> GenerateTitleResponse:
    """Generate a title for a conversation using an LLM."""
    conversation = await load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages_text = _extract_message_texts(conversation.messages)
    if not messages_text:
        return GenerateTitleResponse(title=conversation.title or "Untitled")

    conversation_preview = "\n---\n".join(messages_text)
    instructions = """Generate a short, descriptive title (max 60 characters) for the conversation.
The title should capture the main topic or question.
Return ONLY the title, no quotes or extra text.
"""
    resolved = await prepare_llm_config(request.llm)

    try:
        result = await base_agent.run(
            f"Conversation:\n{conversation_preview}",
            model=create_openai_chat_model(
                resolved.model,
                api_key=resolved.api_key,
                base_url=resolved.base_url,
            ),
            instructions=instructions,
        )
        generated_title = result.output.strip().strip("\"'")
        if len(generated_title) > 100:
            generated_title = generated_title[:97] + "..."

        await set_conversation_title(user.id, conversation_id, generated_title)
        return GenerateTitleResponse(title=generated_title)
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
    user: Annotated[User, Depends(get_current_user)],
) -> CompactConversationResponse:
    """Compact a conversation by summarizing it into a new conversation."""
    llm_config = await prepare_llm_config(request.llm)
    try:
        result = await compact_conversation(user.id, conversation_id, llm_config)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    """Get messages for a conversation in Vercel AI format."""
    messages = await load_messages(user.id, conversation_id)
    if not messages:
        return []
    return VercelAIAdapter.dump_messages(messages)


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
    reasoning_effort = (
        raw_effort
        if raw_effort in ("auto", "none", "low", "medium", "high")
        else "auto"
    )
    included_documents: list[str] = body.get("included_documents") or []
    excluded_documents: list[str] = body.get("excluded_documents") or []
    tools = ToolsSpec(**(body.get("tools") or {}))
    raw_mode = body.get("mode", "execute")
    mode = raw_mode if raw_mode in ("plan", "execute") else "execute"

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
    )


@router.post("/conversations/{conversation_id}/chat")
async def create_conversation_chat(
    conversation_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Handle chat requests using the Vercel AI Data Stream Protocol."""
    config = await _parse_chat_config(request)
    config.conversation_id = conversation_id

    config.llm = await prepare_llm_config(config.llm, default_model=settings.llm.model)
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

    parts.extend([CITATION_INSTRUCTIONS, IMAGE_INSTRUCTIONS, MATH_INSTRUCTIONS])

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

    async def on_complete(result: AgentRunResult[str]) -> None:
        """Persist messages after the agent run completes.

        Must be ``async`` — pydantic-ai runs sync callbacks in a worker
        thread without an event loop, which would break message saving.
        """
        await append_messages(user.id, config.conversation_id, result.all_messages())

    thinking: str | bool

    match config.reasoning_effort:
        case "none":
            thinking = False
        case "auto":
            thinking = True
        case effort:
            thinking = effort

    return await VercelAIAdapter.dispatch_request(
        request,
        agent=user_agent,
        deps=UserDeps(
            user_id=user.id,
            store=store,
            group_stores=user_group_stores,
            document_filter=document_filter,
            group_filters=group_filters,
            llm=config.llm,
        ),
        sdk_version=6,
        output_type=[str, DeferredToolRequests],
        model=create_openai_chat_model(
            config.llm.model,
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
        ),
        toolsets=build_toolsets(
            TOOLSET_GROUPS,
            config.tools,
            extra=[
                build_mcp_server(server)  # .defer_loading()
                for server in config.tools.mcp_servers
            ],
            mode=config.mode,
        ),
        instructions=instructions,
        model_settings=ModelSettings(
            thinking=thinking,
        ),
        on_complete=on_complete,
    )
