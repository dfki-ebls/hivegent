"""Conversation summarization for compaction and subagent recovery.

No token budgeting or truncation is involved.  The conversation being
summarized just (nearly) fit the model's context window — overflow
happens on the latest turn — and the summarization instructions are
far shorter than the chat system prompt, so rendering that same
conversation as a plain transcript usually keeps the request within
the window even with tool and reasoning parts included.  Transcript
fidelity is governed by ``settings.summarization`` for every consumer
alike; dropping tool and reasoning parts there shrinks the transcript
well below what the model already served.  Should a request still
overflow, the error propagates to the caller instead of being retried.
"""

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model

from ..config import settings
from .app import base_agent

__all__ = ["summarize_messages"]

SUMMARY_INSTRUCTIONS = """\
Summarize this conversation concisely.
Include:
- Main topics discussed and questions asked
- Key findings, conclusions, and decisions
- Important document references (filenames)
- Any open questions or action items

Keep the summary under 500 words but comprehensive enough to continue the conversation.
Return ONLY the summary, no extra commentary."""


async def summarize_messages(
    messages: Sequence[ModelMessage],
    model: Model,
) -> str:
    """Summarize *messages* into a concise digest.

    Transcript fidelity (tool and reasoning parts) follows
    ``settings.summarization``.

    Args:
        messages: The conversation messages to summarize.
        model: The model used for summarization.

    Returns:
        A concise summary of the conversation.
    """
    transcript = _format_messages_for_summary(
        messages,
        include_tools=settings.summarization.include_tools,
        include_reasoning=settings.summarization.include_reasoning,
    )
    result = await base_agent.run(
        f"Conversation to summarize:\n\n{transcript}",
        model=model,
        instructions=SUMMARY_INSTRUCTIONS,
    )
    return result.output.strip()


def _format_messages_for_summary(
    messages: Sequence[ModelMessage],
    *,
    include_tools: bool = True,
    include_reasoning: bool = True,
) -> str:
    """Render messages as a plain labeled transcript, untruncated.

    User prompts and assistant text are always kept; tool and
    reasoning parts — the bulk of an agentic conversation's tokens —
    are kept only when the corresponding toggle is on.  Non-text user
    content (attached binaries) is always omitted rather than letting
    a byte payload leak into the transcript via its repr.
    """
    lines: list[str] = []
    for msg in messages:
        for part in msg.parts:
            match part:
                case UserPromptPart(content=content):
                    label = "User"
                    text = (
                        content
                        if isinstance(content, str)
                        else " ".join(c for c in content if isinstance(c, str))
                    )
                case TextPart(content=text):
                    label = "Assistant"
                case ThinkingPart(content=text) if include_reasoning:
                    label = "Reasoning"
                case ToolCallPart(tool_name=tool_name) if include_tools:
                    label = f"Tool call ({tool_name})"
                    text = part.args_as_json_str()
                case ToolReturnPart(tool_name=tool_name) if include_tools:
                    label = f"Tool result ({tool_name})"
                    text = part.model_response_str()
                case _:
                    continue
            if text := text.strip():
                lines.append(f"{label}: {text}")
    return "\n\n".join(lines)
