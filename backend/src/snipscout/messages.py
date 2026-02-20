"""Message persistence utilities."""

import json
from collections.abc import Sequence
from datetime import datetime, timezone

from pydantic_ai.messages import ModelMessage, UserPromptPart

from .config import settings
from .types import ConversationData, ConversationSummary

__all__ = [
    "delete_conversation",
    "list_conversations",
    "load_conversation",
    "load_messages",
    "save_messages",
    "update_conversation_title",
]


def load_conversation(user_id: str, conversation_id: str) -> ConversationData | None:
    """Load a conversation with full metadata.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID.

    Returns:
        The conversation data or None if not found.
    """
    user_dir = settings.get_user_conversations_dir(user_id)
    path = user_dir / f"{conversation_id}.json"
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
    user_dir = settings.get_user_conversations_dir(user_id)
    path = user_dir / f"{conversation_id}.json"
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


def list_conversations(user_id: str) -> list[ConversationSummary]:
    """List all conversations for a user, sorted by most recent first.

    Args:
        user_id: The user ID to list conversations for.

    Returns:
        List of conversation summaries.
    """
    user_dir = settings.get_user_conversations_dir(user_id)
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


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Delete a conversation.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    user_dir = settings.get_user_conversations_dir(user_id)
    path = user_dir / f"{conversation_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def update_conversation_title(user_id: str, conversation_id: str, title: str) -> bool:
    """Update conversation title.

    Args:
        user_id: The user ID that owns the conversation.
        conversation_id: The conversation ID to update.
        title: The new title.

    Returns:
        True if updated, False if not found.
    """
    user_dir = settings.get_user_conversations_dir(user_id)
    path = user_dir / f"{conversation_id}.json"
    if not path.exists():
        return False

    data = json.loads(path.read_bytes())
    data["title"] = title
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_bytes(json.dumps(data, indent=2).encode())
    return True
