"""FastAPI server for the RAG agent."""

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from nanoid import generate
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from starlette.requests import Request
from starlette.responses import Response

from .agent import agent
from .messages import load_messages, save_messages
from .types import CreateConversationResponse

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


def _extract_config(body: dict[str, Any]) -> tuple[str, str, str, str | None]:
    """Extract configuration from request body.

    Args:
        body: The parsed request body.

    Returns:
        A tuple of (conversation_id, model, api_key, base_url) from the request.
    """
    conversation_id: str = body.get("conversationId", "")
    model: str = body.get("model", "")
    api_key: str = body.get("apiKey", "")
    base_url: str | None = body.get("baseUrl") or None
    return conversation_id, model, api_key, base_url


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

    conversation_id, model, api_key, base_url = _extract_config(body)

    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversationId is required")

    # Load existing message history
    message_history = load_messages(conversation_id)

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
        message_history=message_history if message_history else None,
        on_complete=on_complete,
    )
