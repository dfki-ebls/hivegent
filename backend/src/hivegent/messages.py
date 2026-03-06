"""Message persistence utilities."""

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage, UserPromptPart

from .config import settings
from .store import Casebase

__all__ = [
    "ConversationData",
    "ConversationSummary",
    "persist_conversation",
    "find_empty_conversation",
    "list_conversations",
    "load_conversation",
    "load_messages",
    "remove_conversation",
    "save_messages",
    "set_conversation_title",
]


class ConversationData(BaseModel):
    """Full conversation data including messages and metadata."""

    id: str = Field(description="Derived from filename, not persisted", exclude=True)
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    messages: list[ModelMessage] = Field(
        default_factory=list,
        description="Conversation messages",
    )
    compacted_from: str | None = Field(
        default=None,
        description="ID of the conversation this was compacted from",
    )

    @field_validator("messages", mode="before")
    @classmethod
    def _validate_messages(cls, v: Any) -> list[ModelMessage]:
        return ModelMessagesTypeAdapter.validate_python(v)

    @field_serializer("messages")
    @classmethod
    def _serialize_messages(cls, v: list[ModelMessage]) -> Any:
        return ModelMessagesTypeAdapter.dump_python(v, mode="json")


class ConversationSummary(BaseModel):
    """Summary information for listing conversations."""

    id: str = Field(description="Unique conversation ID")
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    message_count: int = Field(description="Number of messages in the conversation")
    compacted_from: str | None = Field(
        default=None,
        description="ID of the conversation this was compacted from",
    )


def load_conversation(user_id: str, conversation_id: str) -> ConversationData | None:
    """Load a conversation with full metadata.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID.

    Returns:
        The conversation data or None if not found.
    """
    path = Casebase.for_user(user_id).conversation_path(
        settings.data_dir, conversation_id
    )
    if not path.exists():
        return None

    data = json.loads(path.read_bytes())
    return ConversationData(id=conversation_id, **data)


def load_messages(user_id: str, conversation_id: str) -> list[ModelMessage]:
    """Load messages for a conversation.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID.

    Returns:
        List of model messages.
    """
    conversation = load_conversation(user_id, conversation_id)
    if not conversation:
        return []
    return list(conversation.messages)


def save_messages(
    user_id: str, conversation_id: str, messages: Sequence[ModelMessage]
) -> None:
    """Save messages for a conversation.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID.
        messages: The messages to save.
    """
    path = Casebase.for_user(user_id).conversation_path(
        settings.data_dir, conversation_id
    )
    now = datetime.now(timezone.utc)

    existing = load_conversation(user_id, conversation_id)
    msgs = list(messages)

    if existing:
        conversation = ConversationData(
            id=conversation_id,
            title=existing.title or _extract_title(msgs),
            created_at=existing.created_at,
            updated_at=now,
            messages=msgs,
            compacted_from=existing.compacted_from,
        )
    else:
        conversation = ConversationData(
            id=conversation_id,
            title=_extract_title(msgs),
            created_at=now,
            updated_at=now,
            messages=msgs,
        )

    path.write_bytes(conversation.model_dump_json(indent=2).encode())


def _extract_title(messages: Sequence[ModelMessage]) -> str:
    """Extract title from first user message content."""
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                text = part.content.strip()
                if text:
                    first_line = text.split("\n")[0]
                    return (
                        first_line[:100]
                        if len(first_line) <= 100
                        else first_line[:97] + "..."
                    )
    return ""


def find_empty_conversation(user_id: str) -> str | None:
    """Find an existing conversation with zero messages.

    Scans the user's conversations directory for a file with an empty
    message list and returns its ID.

    Args:
        user_id: The user ID to search conversations for.

    Returns:
        The conversation ID if an empty one exists, otherwise None.
    """
    user_dir = Casebase.for_user(user_id).conversations_dir(settings.data_dir)
    if not user_dir.exists():
        return None

    for path in user_dir.glob("*.json"):
        conv = load_conversation(user_id, path.stem)
        if conv and len(conv.messages) == 0:
            return conv.id

    return None


def persist_conversation(user_id: str, conversation_id: str) -> None:
    """Persist an empty conversation file.

    Creates a new conversation JSON file with no messages.  The file
    serves as proof that the ID was issued by the server.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID to create.
    """
    path = Casebase.for_user(user_id).conversation_path(
        settings.data_dir, conversation_id
    )
    now = datetime.now(timezone.utc)
    conversation = ConversationData(
        id=conversation_id,
        title="",
        created_at=now,
        updated_at=now,
        messages=[],
    )
    path.write_bytes(conversation.model_dump_json(indent=2).encode())


def list_conversations(user_id: str) -> list[ConversationSummary]:
    """List all conversations for a user, sorted by most recent first.

    Args:
        user_id: The user ID to list conversations for.

    Returns:
        List of conversation summaries.
    """
    user_dir = Casebase.for_user(user_id).conversations_dir(settings.data_dir)
    if not user_dir.exists():
        return []

    conversations = []
    for path in user_dir.glob("*.json"):
        try:
            conv = load_conversation(user_id, path.stem)
            if conv:
                conversations.append(
                    ConversationSummary(
                        id=conv.id,
                        title=conv.title,
                        created_at=conv.created_at,
                        updated_at=conv.updated_at,
                        message_count=len(conv.messages),
                        compacted_from=conv.compacted_from,
                    )
                )
        except Exception:
            continue

    conversations.sort(key=lambda c: c.updated_at, reverse=True)
    return conversations


def remove_conversation(user_id: str, conversation_id: str) -> bool:
    """Delete a conversation.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    path = Casebase.for_user(user_id).conversation_path(
        settings.data_dir, conversation_id
    )
    if path.exists():
        path.unlink()
        return True
    return False


def set_conversation_title(user_id: str, conversation_id: str, title: str) -> bool:
    """Update conversation title.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID to update.
        title: The new title.

    Returns:
        True if updated, False if not found.
    """
    path = Casebase.for_user(user_id).conversation_path(
        settings.data_dir, conversation_id
    )
    if not path.exists():
        return False

    data = json.loads(path.read_bytes())
    data["title"] = title
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_bytes(json.dumps(data, indent=2).encode())
    return True
