"""Message persistence utilities."""

import json
from datetime import datetime, timezone

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


def load_conversation(conversation_id: str) -> ConversationData | None:
    """Load a conversation with full metadata."""
    path = settings.conversations_dir / f"{conversation_id}.json"
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


def load_messages(conversation_id: str) -> list[ModelMessage]:
    """Load messages for a conversation."""
    conversation = load_conversation(conversation_id)
    if not conversation:
        return []
    return ModelMessagesTypeAdapter.validate_json(json.dumps(conversation.messages))


def save_messages(conversation_id: str, messages: list[ModelMessage]) -> None:
    """Save messages for a conversation."""
    settings.conversations_dir.mkdir(parents=True, exist_ok=True)
    path = settings.conversations_dir / f"{conversation_id}.json"
    now = datetime.now(timezone.utc)

    existing = load_conversation(conversation_id)
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


def _extract_title(messages: list[dict]) -> str:
    """Extract title from first user message content."""
    for msg in messages:
        for part in msg.get("parts", []):
            content = part.get("content")
            if isinstance(content, str) and content.strip():
                first_line = content.strip().split("\n")[0]
                return first_line[:100] if len(first_line) <= 100 else first_line[:97] + "..."
    return ""


def _extract_document_refs(messages: list[dict]) -> list[DocumentReference]:
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


def list_conversations() -> list[ConversationSummary]:
    """List all conversations, sorted by most recent first."""
    if not settings.conversations_dir.exists():
        return []

    conversations = []
    for path in settings.conversations_dir.glob("*.json"):
        try:
            conv = load_conversation(path.stem)
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


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation. Returns True if deleted."""
    path = settings.conversations_dir / f"{conversation_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def update_conversation_title(conversation_id: str, title: str) -> bool:
    """Update conversation title. Returns True if updated."""
    path = settings.conversations_dir / f"{conversation_id}.json"
    if not path.exists():
        return False

    data = json.loads(path.read_bytes())
    if isinstance(data, dict):
        data["title"] = title
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_bytes(json.dumps(data, indent=2).encode())
        return True
    return False
