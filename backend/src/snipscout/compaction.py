"""Conversation compaction service.

Summarizes long conversations and creates new conversations with the summary
as initial context, linking back to the original.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from nanoid import generate
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .agent import base_agent
from .config import settings
from .messages import load_conversation
from .types import ConversationData, LlmConfig

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
    llm_config: LlmConfig,
) -> CompactionResult:
    """Compact a conversation by summarizing it into a new conversation.

    Loads the original conversation, generates a summary using a lightweight
    LLM, and creates a new conversation with the summary as the initial
    system context. The new conversation links back to the original via
    ``compacted_from``.

    Args:
        user_id: The user who owns the conversation.
        conversation_id: The conversation to compact.
        llm_config: LLM configuration for the summarization model.

    Returns:
        A CompactionResult with the new conversation ID and summary.

    Raises:
        ValueError: If the conversation is not found or has no messages.
    """
    conversation = load_conversation(user_id, conversation_id)
    if not conversation or not conversation.messages:
        raise ValueError(f"Conversation {conversation_id} not found or empty")

    summary = await _summarize_conversation(conversation.messages, llm_config)

    new_id = generate()
    now = datetime.now(timezone.utc)

    summary_messages: list[ModelMessage] = [
        ModelResponse(parts=[TextPart(content=summary)]),
    ]

    original_title = conversation.title or "Untitled"
    new_conversation = ConversationData(
        id=new_id,
        title=f"{original_title} (continued)",
        created_at=now,
        updated_at=now,
        document_references=conversation.document_references,
        messages=summary_messages,
        compacted_from=conversation_id,
    )

    conversations_dir = settings.get_user_conversations_dir(user_id)
    path = conversations_dir / f"{new_id}.json"
    path.write_bytes(new_conversation.model_dump_json(indent=2).encode())

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
        model=OpenAIResponsesModel(
            llm_config.model,
            provider=OpenAIProvider(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
            ),
        ),
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
