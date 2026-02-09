"""Message persistence utilities."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_json

from .config import settings
from .types import ConversationData, ConversationSummary, DocumentReference

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

    # Handle legacy format (just messages array)
    if isinstance(data, list):
        now = datetime.now(timezone.utc)
        return ConversationData(
            id=conversation_id,
            title="",
            created_at=now,
            updated_at=now,
            document_references=[],
            messages=data,
        )

    return ConversationData(**data)


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
    return ModelMessagesTypeAdapter.validate_json(json.dumps(conversation.messages))


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
    messages_data = json.loads(to_json(messages))

    if existing:
        conversation = ConversationData(
            id=conversation_id,
            title=existing.title or _extract_title(messages_data),
            created_at=existing.created_at,
            updated_at=now,
            document_references=_extract_document_refs(messages_data),
            messages=messages_data,
        )
    else:
        conversation = ConversationData(
            id=conversation_id,
            title=_extract_title(messages_data),
            created_at=now,
            updated_at=now,
            document_references=_extract_document_refs(messages_data),
            messages=messages_data,
        )

    path.write_bytes(conversation.model_dump_json(indent=2).encode())


def _extract_title(messages: Sequence[Mapping[str, Any]]) -> str:
    """Extract title from first user message content."""
    for msg in messages:
        for part in msg.get("parts", []):
            content = part.get("content")
            if isinstance(content, str) and content.strip():
                first_line = content.strip().split("\n")[0]
                return first_line[:100] if len(first_line) <= 100 else first_line[:97] + "..."
    return ""


def _extract_document_refs(messages: Sequence[Mapping[str, Any]]) -> list[DocumentReference]:
    """Extract document references from tool calls."""
    refs: dict[str, list[str]] = {}

    for msg in messages:
        for part in msg.get("parts", []):
            tool_name = part.get("tool_name")
            args = part.get("args", {})
            if not tool_name or not isinstance(args, dict):
                continue

            filename = args.get("filename")
            if filename:
                refs.setdefault(filename, []).append(tool_name)

    return [
        DocumentReference(filename=fn, sources=list(set(sources)))
        for fn, sources in refs.items()
    ]


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
    if isinstance(data, dict):
        data["title"] = title
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_bytes(json.dumps(data, indent=2).encode())
        return True
    return False
