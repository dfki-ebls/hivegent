"""What the app reads off a conversation's messages: prompts, titles, and the metadata marking them."""

from collections.abc import Sequence
from typing import TypeIs

from exar.text import truncate_line
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponsePart,
    TextContent,
    UserContent,
    UserPromptPart,
)
from pydantic_ai_harness.sub_agents import is_task_report

from ...shared.l10n import Localized

__all__ = [
    "CHAT_ERROR_KEY",
    "HIDDEN_KEY",
    "UNTITLED",
    "extract_title",
    "is_hidden",
    "is_prompt",
    "is_user_request",
    "prompt_items",
    "prompt_text",
    "render_prompt",
    "title_line",
]

# Message metadata key carrying a run's error text so the frontend can re-render
# the chat error banner from reloaded history (a stream error is otherwise a
# transient chunk, not a message part, and is lost on reload).
CHAT_ERROR_KEY = "chatError"

# Server-only message metadata key marking a message the transcript leaves out,
# one the application wrote for the model rather than one the user saw stream:
# the memory read a window opens with.
HIDDEN_KEY = "hidden"

UNTITLED = Localized(en="Untitled", de="Ohne Titel")
"""The title shown for a conversation that has none."""


def is_hidden(message: ModelMessage) -> bool:
    """Whether the transcript leaves *message* out (:data:`HIDDEN_KEY`)."""
    return bool((message.metadata or {}).get(HIDDEN_KEY))


def is_prompt(part: ModelRequestPart | ModelResponsePart) -> TypeIs[UserPromptPart]:
    """Whether *part* is a prompt of the user, not a subagent's report or question delivered as one."""
    return isinstance(part, UserPromptPart) and not is_task_report(part)


def is_user_request(msg: ModelMessage) -> bool:
    """Whether *msg* is a user turn (a request carrying a user prompt).

    Tool-return-only requests in the middle of an agent loop are not user
    turns, and neither are the subagent reports a run delivers (:func:`is_prompt`).
    """
    return isinstance(msg, ModelRequest) and any(is_prompt(part) for part in msg.parts)


def prompt_items(part: UserPromptPart) -> Sequence[UserContent]:
    """The items of a user prompt, a bare text being its only one."""
    return (part.content,) if isinstance(part.content, str) else part.content


def prompt_text(item: UserContent) -> str | None:
    """The text of a user prompt item, ``None`` for an attachment."""
    if isinstance(item, str):
        return item

    if isinstance(item, TextContent):
        return item.content

    return None


def render_prompt(part: UserPromptPart) -> str:
    """*part*'s text, each attachment standing in as ``[attachment]``."""
    return "\n".join(
        "[attachment]" if (line := prompt_text(item)) is None else line
        for item in prompt_items(part)
    )


def title_line(text: str) -> str:
    """The first line of *text* as a title, clipped to 100 characters.

    >>> title_line("Bees\\nand more"), len(title_line("a" * 200))
    ('Bees', 100)
    """
    return truncate_line(text.strip().split("\n", 1)[0], 100)


def extract_title(messages: Sequence[ModelMessage]) -> str | None:
    """Pull a one-line title from the first user prompt text, if any."""
    texts = (
        prompt_text(item)
        for msg in messages
        for part in msg.parts
        if is_prompt(part)
        for item in prompt_items(part)
    )

    for text in texts:
        if text and text.strip():
            return title_line(text)

    return None
