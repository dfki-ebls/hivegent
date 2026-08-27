"""Conversation compaction service.

Summarizes long conversations and creates new conversations with the summary
as initial context, linking back to the original.
"""

import logging
from dataclasses import dataclass

from pydantic_ai.messages import ModelResponse, TextPart

from .agents import RunPrefix
from .agents.summarize import summarize_conversation
from .db.conversations import create_compacted_conversation, load_conversation

__all__ = [
    "CompactionResult",
    "compact_conversation",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CompactionResult:
    """Result of a conversation compaction."""

    new_conversation_id: str
    summary: str


async def compact_conversation(
    user_id: str,
    conversation_id: str,
    run: RunPrefix,
) -> CompactionResult:
    """Compact a conversation by summarizing it into a new conversation.

    Generates a summary of the conversation's persisted active path with the
    regular chat model (the conversation being compacted typically overflows a
    small model's context) and creates a new conversation with the summary as
    the initial context.

    The messages are read from SQL rather than posted by the browser, for the
    same reason a chat turn replays them from SQL: that history is what the
    provider was sent and therefore what it cached, and the summary is asked
    for as one more turn of it (see
    :func:`~hivegent.agents.summarize.summarize_conversation`).  A projection
    through the Vercel UI message shapes and back would reproduce it only as
    faithfully as the adapter transcribes, and would arrive stripped of the
    per-turn usage the retry plan is sized from.

    *run* is the prompt prefix the conversation's own turns ran under, which is
    the other half of that prefix.

    Args:
        user_id: The user who owns the conversation.
        conversation_id: The conversation being compacted.
        run: The prompt prefix the conversation's turns ran under.

    Returns:
        A CompactionResult with the new conversation ID and summary.

    Raises:
        ValueError: If the conversation is missing, not owned, or empty.
    """
    conversation = await load_conversation(user_id, conversation_id)
    if conversation is None or not conversation.messages:
        raise ValueError(f"Conversation {conversation_id} not found or empty")

    summary = await summarize_conversation(conversation.messages, run)

    summary_message = ModelResponse(parts=[TextPart(content=summary)])
    new_id = await create_compacted_conversation(
        user_id,
        original_conversation_id=conversation_id,
        summary_message=summary_message,
        title=f"{conversation.title or 'Untitled'} (continued)",
    )

    logger.info(
        "Compacted conversation %s -> %s for user %s",
        conversation_id,
        new_id,
        user_id,
    )

    return CompactionResult(
        new_conversation_id=new_id,
        summary=summary,
    )
