"""Conversation compaction service.

Summarizes long conversations and creates new conversations with the summary
as initial context, linking back to the original.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

from .agents.summarize import summarize_messages
from .db.conversations import (
    conversation_exists,
    create_compacted_conversation,
    extract_title,
)
from .llm import model_from_config, summary_model_settings
from .llm_config import LlmConfig

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
    messages: Sequence[ModelMessage],
    llm_config: LlmConfig,
) -> CompactionResult:
    """Compact a conversation by summarizing it into a new conversation.

    Generates a summary of the supplied messages with the regular chat
    model (the conversation being compacted typically overflows a small
    model's context) and creates a new conversation with the summary as
    the initial context.
    The messages come from the client rather than the database so the summary
    reflects the exact branch and partial turn visible to the user.

    The new conversation links back to the original via ``compacted_from``
    only when the original is a persisted row — a freshly minted draft has
    no row to reference yet.

    Args:
        user_id: The user who owns the conversation.
        conversation_id: The conversation being compacted.
        messages: The conversation messages to summarize.
        llm_config: LLM configuration for the summarization model.

    Returns:
        A CompactionResult with the new conversation ID and summary.

    Raises:
        ValueError: If no messages are supplied.
    """
    if not messages:
        raise ValueError(f"Conversation {conversation_id} not found or empty")

    summary = await summarize_messages(
        messages,
        model_from_config(llm_config),
        summary_model_settings(llm_config),
    )

    base_title = extract_title(messages) or "Untitled"
    summary_message = ModelResponse(parts=[TextPart(content=summary)])
    new_id = await create_compacted_conversation(
        user_id,
        original_conversation_id=(
            conversation_id
            if await conversation_exists(user_id, conversation_id)
            else None
        ),
        summary_message=summary_message,
        title=f"{base_title} (continued)",
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
