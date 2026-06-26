"""Conversation summarization for compaction and subagent recovery.

No token budgeting or truncation is involved.  The conversation being
summarized just (nearly) fit the model's context window — overflow
happens on the latest turn — and the summarization instructions are
far shorter than the chat system prompt, so rendering that same
conversation as a plain transcript usually keeps the request within
the window even with tool and reasoning parts included.  Transcript
fidelity for the first attempt is governed by ``settings.summarization``
for every consumer alike.

Should that full-fidelity request still overflow — an agentic turn that
reads several large documents leaves tool results nearly as large as the
conversation that already overflowed — it is retried once with the heavy
parts shed: tool results and reasoning go, while tool calls stay so the
summary keeps the document filenames it references.  Those parts are the
bulk of an agentic conversation's tokens, so the reduced transcript falls
well below what the model already served.  A rejection that is not a
context overflow, or an overflow that persists with nothing left to shed,
propagates to the caller.
"""

from collections.abc import Sequence

from pydantic_ai.exceptions import ModelHTTPError
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
from ..llm import is_context_overflow
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

    The first attempt renders the transcript at the fidelity configured by
    ``settings.summarization``.  If that overflows the model's own context
    window, it retries once with tool results and reasoning dropped — tool
    calls stay, so the summary keeps the document filenames it references.

    Args:
        messages: The conversation messages to summarize.
        model: The model used for summarization.

    Returns:
        A concise summary of the conversation.

    Raises:
        ModelHTTPError: If the model rejects the request for a reason other
            than context overflow, or the reduced transcript still overflows.
    """
    include_tools = settings.summarization.include_tools
    include_reasoning = settings.summarization.include_reasoning

    try:
        return await _run_summary(
            messages,
            model,
            include_tool_calls=include_tools,
            include_tool_results=include_tools,
            include_reasoning=include_reasoning,
        )

    except ModelHTTPError as exc:
        # Only a full transcript overflowing the model's own window is
        # recoverable here, and only while heavy parts remain to shed.
        if not is_context_overflow(exc) or not (include_tools or include_reasoning):
            raise

    return await _run_summary(
        messages,
        model,
        include_tool_calls=include_tools,
        include_tool_results=False,
        include_reasoning=False,
    )


async def _run_summary(
    messages: Sequence[ModelMessage],
    model: Model,
    *,
    include_tool_calls: bool,
    include_tool_results: bool,
    include_reasoning: bool,
) -> str:
    """Render *messages* at the given fidelity and summarize them once."""
    transcript = _format_messages_for_summary(
        messages,
        include_tool_calls=include_tool_calls,
        include_tool_results=include_tool_results,
        include_reasoning=include_reasoning,
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
    include_tool_calls: bool = True,
    include_tool_results: bool = True,
    include_reasoning: bool = True,
) -> str:
    """Render messages as a plain labeled transcript, untruncated.

    User prompts and assistant text are always kept.  Tool calls, tool
    results, and reasoning parts — the bulk of an agentic conversation's
    tokens, with tool results (full document bodies) the heaviest — are
    each kept only when their toggle is on.  Non-text user content
    (attached binaries) is always omitted rather than letting a byte
    payload leak into the transcript via its repr.
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
                case ToolCallPart(tool_name=tool_name) if include_tool_calls:
                    label = f"Tool call ({tool_name})"
                    text = part.args_as_json_str()
                case ToolReturnPart(tool_name=tool_name) if include_tool_results:
                    label = f"Tool result ({tool_name})"
                    text = part.model_response_str()
                case _:
                    continue
            if text := text.strip():
                lines.append(f"{label}: {text}")
    return "\n\n".join(lines)
