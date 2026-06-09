"""Conversation compaction service.

Summarizes long conversations and creates new conversations with the summary
as initial context, linking back to the original.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from .agents import base_agent
from .db.conversations import (
    conversation_exists,
    create_compacted_conversation,
    extract_title,
)
from .llm import model_from_config
from .types import LlmConfig

__all__ = [
    "CompactionResult",
    "compact_conversation",
]

logger = logging.getLogger(__name__)

COMPACTION_INSTRUCTIONS = """\
Summarize this conversation concisely.
Include:
- Main topics discussed and questions asked
- Key findings, conclusions, and decisions
- Important document references (filenames)
- Any open questions or action items

Keep the summary under 500 words but comprehensive enough to continue the conversation.
Return ONLY the summary, no extra commentary."""


@dataclass(slots=True, frozen=True)
class CompactionResult:
    """Result of a conversation compaction."""

    new_conversation_id: str
    summary: str
    original_conversation_id: str


async def compact_conversation(
    user_id: str,
    conversation_id: str,
    messages: Sequence[ModelMessage],
    llm_config: LlmConfig,
) -> CompactionResult:
    """Compact a conversation by summarizing it into a new conversation.

    Generates a summary of the supplied messages using a lightweight LLM
    and creates a new conversation with the summary as the initial context.
    The messages come from the client rather than the database: the turn
    that triggers auto-compaction fails on a context-length error and is
    never persisted, so the database copy would be stale or missing.

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

    summary = await _summarize_conversation(messages, llm_config)

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
        original_conversation_id=conversation_id,
    )


async def _summarize_conversation(
    messages: Sequence[ModelMessage],
    llm_config: LlmConfig,
) -> str:
    """Summarize conversation messages using a lightweight model.

    Args:
        messages: The conversation messages to summarize.
        llm_config: LLM configuration with resolved credentials.

    Returns:
        A concise summary of the conversation.
    """
    conversation_text = _format_messages_for_summary(messages)

    result = await base_agent.run(
        f"Conversation to summarize:\n\n{conversation_text}",
        model=model_from_config(llm_config),
        instructions=COMPACTION_INSTRUCTIONS,
    )

    return result.output.strip()


def _format_messages_for_summary(
    messages: Sequence[ModelMessage],
    max_chars: int = 20_000,
) -> str:
    """Format messages into readable text for summarization.

    Extracts user and assistant text content, truncating to stay within
    a character budget so the summary request itself fits in context.

    Args:
        messages: The conversation messages.
        max_chars: Maximum total characters to include.

    Returns:
        A formatted string of the conversation.
    """
    lines: list[str] = []
    total = 0

    for msg in messages:
        role = msg.kind
        for part in msg.parts:
            if isinstance(part, UserPromptPart):
                content = (
                    part.content if isinstance(part.content, str) else str(part.content)
                )
            elif isinstance(part, TextPart):
                content = part.content
            else:
                continue

            text = content.strip()
            if not text:
                continue

            label = "User" if role == "request" else "Assistant"
            line = f"{label}: {text[:2000]}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)

    return "\n\n".join(lines)
