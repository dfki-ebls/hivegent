"""FastAPI server for the RAG agent."""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from nanoid import generate
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .agent import agent
from .config import FileExtension, settings
from .documents import reload_documents
from .messages import load_messages, save_messages
from .types import (
    CreateConversationResponse,
    DeleteDocumentResponse,
    DocumentInfo,
    DocumentListResponse,
    UploadDocumentResponse,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/conversation")
async def create_conversation() -> CreateConversationResponse:
    """Create a new conversation and return its ID."""
    conversation_id = generate()
    return CreateConversationResponse(id=conversation_id)


def _extract_config(body: dict[str, Any]) -> tuple[str, str, str, str | None, str | None]:
    """Extract configuration from request body.

    Args:
        body: The parsed request body.

    Returns:
        A tuple of (conversation_id, model, api_key, base_url, system_prompt).
    """
    conversation_id: str = body.get("conversationId", "")
    model: str = body.get("model", "")
    api_key: str = body.get("apiKey", "")
    base_url: str | None = body.get("baseUrl") or None
    system_prompt: str | None = body.get("systemPrompt") or None
    return conversation_id, model, api_key, base_url, system_prompt


@app.get("/api/conversation/{conversation_id}/messages")
async def get_messages(conversation_id: str) -> list[UIMessage]:
    """Get messages for a conversation in Vercel AI format."""
    messages = load_messages(conversation_id)
    if not messages:
        return []
    return VercelAIAdapter.dump_messages(messages)


@app.post("/api/chat")
async def chat(request: Request) -> Response:
    """Handle chat requests using the Vercel AI Data Stream Protocol.

    The request body must include:
    - conversationId: The conversation ID for persistence
    - model: The model identifier (e.g., "openai/gpt-4o")
    - apiKey: The API key for the LLM provider
    - baseUrl: Optional custom base URL for the LLM provider
    """
    body_bytes = await request.body()
    body: dict[str, Any] = {}
    if body_bytes:
        body = json.loads(body_bytes)

    conversation_id, model, api_key, base_url, system_prompt = _extract_config(body)

    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversationId is required")

    # Load existing message history
    message_history: list[ModelMessage] | None = load_messages(conversation_id)

    # For new conversations with a custom system prompt, prepend it to history
    # This ensures the custom prompt is used instead of the agent's default instructions
    if not message_history and system_prompt:
        message_history = [ModelRequest(parts=[SystemPromptPart(content=system_prompt)])]

    def on_complete(result: AgentRunResult[str]) -> None:
        """Save messages after the agent run completes."""
        save_messages(conversation_id, result.all_messages())

    modified_request = Request(
        scope=request.scope,
        receive=request.receive,
    )
    modified_request._body = body_bytes

    return await VercelAIAdapter.dispatch_request(
        modified_request,
        agent=agent,
        deps=None,
        model=OpenAIResponsesModel(
            model,
            provider=OpenAIProvider(
                api_key=api_key,
                base_url=base_url if base_url else None,
            ),
        ),
        message_history=message_history,
        on_complete=on_complete,
    )


def _get_allowed_extensions() -> set[str]:
    """Get the set of allowed file extensions."""
    return {ext.value for ext in FileExtension}


@app.get("/api/documents")
async def list_documents() -> DocumentListResponse:
    """List all documents in the data directory."""
    data_dir = settings.data_dir
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
async def upload_document(filename: str, file: UploadFile) -> UploadDocumentResponse:
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

    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    file_path = data_dir / filename
    file_path.write_bytes(content)

    reload_documents()

    return UploadDocumentResponse(
        filename=filename,
        size_bytes=len(content),
        message="Document uploaded successfully",
    )


@app.get("/api/documents/{filename}")
async def get_document_content(filename: str) -> PlainTextResponse:
    """Get the content of a document.

    Args:
        filename: The filename to read.
    """
    file_path = settings.data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    allowed_extensions = _get_allowed_extensions()
    if file_path.suffix.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    return PlainTextResponse(file_path.read_text(encoding="utf-8"))


@app.delete("/api/documents/{filename}")
async def delete_document(filename: str) -> DeleteDocumentResponse:
    """Delete a document.

    Args:
        filename: The filename to delete.
    """
    file_path = settings.data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    allowed_extensions = _get_allowed_extensions()
    if file_path.suffix.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail="Invalid file type")

    file_path.unlink()

    reload_documents()

    return DeleteDocumentResponse(
        filename=filename,
        message="Document deleted successfully",
    )
