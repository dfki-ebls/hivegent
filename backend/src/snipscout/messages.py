"""Message persistence utilities."""

from pathlib import Path

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_json

__all__ = ["load_messages", "save_messages"]

MESSAGES_DIR = Path("messages")


def _get_message_path(conversation_id: str) -> Path:
    """Get the path for a conversation's messages file."""
    return MESSAGES_DIR / f"{conversation_id}.json"


def load_messages(conversation_id: str) -> list[ModelMessage]:
    """Load messages for a conversation.

    Args:
        conversation_id: The conversation ID.

    Returns:
        List of messages, or empty list if conversation doesn't exist.
    """
    path = _get_message_path(conversation_id)
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
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = _get_message_path(conversation_id)
    json_bytes = to_json(messages, indent=2)
    path.write_bytes(json_bytes)
