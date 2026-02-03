"""Message persistence utilities."""

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_json

from .config import settings

__all__ = ["load_messages", "save_messages"]


def load_messages(conversation_id: str) -> list[ModelMessage]:
    """Load messages for a conversation.

    Args:
        conversation_id: The conversation ID.

    Returns:
        List of messages, or empty list if conversation doesn't exist.
    """
    path = settings.conversations_dir / f"{conversation_id}.json"
    if not path.exists():
        return []

    json_bytes = path.read_bytes()
    return ModelMessagesTypeAdapter.validate_json(json_bytes)


def save_messages(conversation_id: str, messages: list[ModelMessage]) -> None:
    """Save messages for a conversation.

    Args:
        conversation_id: The conversation ID.
        messages: The messages to save.
    """
    settings.conversations_dir.mkdir(parents=True, exist_ok=True)
    path = settings.conversations_dir / f"{conversation_id}.json"
    json_bytes = to_json(messages, indent=2)
    path.write_bytes(json_bytes)
