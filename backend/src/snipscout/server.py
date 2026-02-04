"""FastAPI server for the RAG agent."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from nanoid import generate
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .agent import AgentDeps, agent, small_agent
from .auth import User, get_current_user
from .config import FileExtension, settings
from .documents import reload_user_documents
from .messages import (
    delete_conversation,
    list_conversations,
    load_conversation,
    load_messages,
    save_messages,
    update_conversation_title,
)
from .prompts import PERSONALITY_TEMPLATES
from .tokens import token_store
from .types import (
    ChatRequestConfig,
    ConversationListResponse,
    ConversationSummary,
    CreateConversationResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    DeleteConversationResponse,
    DeleteDocumentResponse,
    DocumentInfo,
    DocumentListResponse,
    GenerateTitleRequest,
    GenerateTitleResponse,
    Personality,
    TokenInfo,
    UpdateTitleRequest,
    UploadDocumentResponse,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/conversation")
async def create_conversation(
    user: Annotated[User, Depends(get_current_user)],
) -> CreateConversationResponse:
    """Create a new conversation and return its ID."""
    conversation_id = generate()
    return CreateConversationResponse(id=conversation_id)


@app.get("/api/conversations")
async def get_conversations(
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationListResponse:
    """List all conversations with summary information."""
    conversations = list_conversations(user.id)
    return ConversationListResponse(
        conversations=conversations,
        total_count=len(conversations),
    )


@app.get("/api/conversation/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Get summary information for a specific conversation."""
    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(conversation.messages),
    )


@app.get("/api/conversation/{conversation_id}/document-references")
async def get_conversation_document_references(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    """Get document references for a conversation."""
    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [ref.model_dump() for ref in conversation.document_references]


@app.put("/api/conversation/{conversation_id}/title")
async def update_title(
    conversation_id: str,
    request: UpdateTitleRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    """Update the title of a conversation."""
    if not update_conversation_title(user.id, conversation_id, request.title):
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(conversation.messages),
    )


def _extract_message_texts(
    messages: list[ModelMessage], max_messages: int = 4
) -> list[str]:
    """Extract text content from conversation messages."""
    texts: list[str] = []
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, (UserPromptPart, TextPart)):
                if isinstance(part.content, str) and (text := part.content.strip()):
                    texts.append(text[:500])
                    if len(texts) >= max_messages:
                        return texts
    return texts


@app.post("/api/conversation/{conversation_id}/generate-title")
async def generate_title(
    conversation_id: str,
    request: GenerateTitleRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> GenerateTitleResponse:
    """Generate a title for a conversation using an LLM."""
    conversation = load_conversation(user.id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = ModelMessagesTypeAdapter.validate_python(conversation.messages)
    messages_text = _extract_message_texts(messages)
    if not messages_text:
        return GenerateTitleResponse(title=conversation.title or "Untitled")

    conversation_preview = "\n---\n".join(messages_text)

    instructions = """Generate a short, descriptive title (max 60 characters) for the conversation.
The title should capture the main topic or question.
Return ONLY the title, no quotes or extra text.
"""

    try:
        result = await small_agent.run(
            f"Conversation:\n{conversation_preview}",
            model=OpenAIResponsesModel(
                request.model,
                provider=OpenAIProvider(
                    api_key=request.api_key or "not-needed",
                    base_url=request.base_url,
                ),
            ),
            instructions=instructions,
        )

        generated_title = result.output.strip().strip("\"'")
        if len(generated_title) > 100:
            generated_title = generated_title[:97] + "..."

        update_conversation_title(user.id, conversation_id, generated_title)

        return GenerateTitleResponse(title=generated_title)

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate title: {str(e)}"
        )


@app.delete("/api/conversation/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteConversationResponse:
    """Delete a conversation."""
    if not delete_conversation(user.id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return DeleteConversationResponse(
        id=conversation_id,
        message="Conversation deleted successfully",
    )


@app.get("/api/conversation/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> list[UIMessage]:
    """Get messages for a conversation in Vercel AI format."""
    messages = load_messages(user.id, conversation_id)
    if not messages:
        return []
    return VercelAIAdapter.dump_messages(messages)


def _parse_chat_config(request: Request) -> ChatRequestConfig:
    """Parse chat configuration from request headers.

    Args:
        request: The incoming HTTP request.

    Returns:
        A ChatRequestConfig instance populated from headers.
    """
    personality_header = request.headers.get("x-personality", "")
    try:
        personality = (
            Personality(personality_header)
            if personality_header
            else Personality.DEFAULT
        )
    except ValueError:
        personality = Personality.DEFAULT

    return ChatRequestConfig(
        conversation_id=request.headers.get("x-conversation-id", ""),
        model=request.headers.get("x-model", ""),
        api_key=request.headers.get("x-api-key", ""),
        base_url=request.headers.get("x-base-url") or None,
        personality=personality,
    )


@app.post("/api/chat")
async def chat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Handle chat requests using the Vercel AI Data Stream Protocol.

    Configuration is passed via HTTP headers (see ChatRequestConfig).
    """
    config = _parse_chat_config(request)

    if not config.conversation_id:
        raise HTTPException(
            status_code=400, detail="x-conversation-id header is required"
        )

    # Load existing message history
    message_history: list[ModelMessage] | None = load_messages(
        user.id, config.conversation_id
    )

    # For new conversations, prepend the personality's system prompt to history
    if not message_history:
        system_prompt = PERSONALITY_TEMPLATES[config.personality]
        message_history = [
            ModelRequest(parts=[SystemPromptPart(content=system_prompt)])
        ]

    def on_complete(result: AgentRunResult[str]) -> None:
        """Save messages after the agent run completes."""
        save_messages(user.id, config.conversation_id, result.all_messages())

    return await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=AgentDeps(user_id=user.id),
        model=OpenAIResponsesModel(
            config.model,
            provider=OpenAIProvider(
                api_key=config.api_key,
                base_url=config.base_url,
            ),
        ),
        message_history=message_history,
        on_complete=on_complete,
    )


def _get_allowed_extensions() -> set[str]:
    """Get the set of allowed file extensions."""
    return {ext.value for ext in FileExtension}


@app.get("/api/documents")
async def list_documents(
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentListResponse:
    """List all documents in the user's data directory."""
    data_dir = settings.get_user_documents_dir(user.id)
    documents: list[DocumentInfo] = []

    if data_dir.exists():
        allowed_extensions = _get_allowed_extensions()
        for file_path in sorted(data_dir.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in allowed_extensions:
                stat = file_path.stat()
                documents.append(
                    DocumentInfo(
                        filename=file_path.name,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                    )
                )

    return DocumentListResponse(documents=documents, total_count=len(documents))


@app.put("/api/documents/{filename}")
async def upload_document(
    filename: str,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
) -> UploadDocumentResponse:
    """Upload or replace a document.

    Args:
        filename: The target filename (must have allowed extension).
        file: The uploaded file content.
    """
    allowed_extensions = _get_allowed_extensions()
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file extension. Allowed: {', '.join(sorted(allowed_extensions))}",
        )

    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_bytes} bytes",
        )

    data_dir = settings.get_user_documents_dir(user.id)

    file_path = data_dir / filename
    file_path.write_bytes(content)

    reload_user_documents(user.id)

    return UploadDocumentResponse(
        filename=filename,
        size_bytes=len(content),
        message="Document uploaded successfully",
    )


@app.get("/api/documents/{filename}")
async def get_document_content(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
) -> PlainTextResponse:
    """Get the content of a document.

    Args:
        filename: The filename to read.
    """
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    allowed_extensions = _get_allowed_extensions()
    if file_path.suffix.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    return PlainTextResponse(file_path.read_text(encoding="utf-8"))


@app.delete("/api/documents/{filename}")
async def delete_document(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDocumentResponse:
    """Delete a document.

    Args:
        filename: The filename to delete.
    """
    data_dir = settings.get_user_documents_dir(user.id)
    file_path = data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    allowed_extensions = _get_allowed_extensions()
    if file_path.suffix.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    file_path.unlink()

    reload_user_documents(user.id)

    return DeleteDocumentResponse(
        filename=filename,
        message="Document deleted successfully",
    )


# Token management endpoints


@app.post("/api/tokens")
async def create_token(
    request: CreateTokenRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateTokenResponse:
    """Create a new personal access token.

    The raw token is only returned once and cannot be retrieved later.
    """
    raw_token, token_info = token_store.create_token(
        user_id=user.id,
        name=request.name,
        expires_in_days=request.expires_in_days,
    )

    return CreateTokenResponse(
        token=raw_token,
        id=token_info.id,
        name=token_info.name,
        created_at=token_info.created_at,
        expires_at=token_info.expires_at,
    )


@app.get("/api/tokens")
async def list_tokens(
    user: Annotated[User, Depends(get_current_user)],
) -> list[TokenInfo]:
    """List all personal access tokens for the current user."""
    tokens = token_store.list_tokens(user.id)
    return [
        TokenInfo(
            id=t.id,
            name=t.name,
            created_at=t.created_at,
            expires_at=t.expires_at,
            last_used_at=t.last_used_at,
        )
        for t in tokens
    ]


@app.delete("/api/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke a personal access token."""
    if not token_store.revoke_token(user.id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")
