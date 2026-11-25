"""Tests for the stored messages :class:`PostgresConversationStore` keeps in memory."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic_ai.conversation_store import ConversationEntry
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_core import to_json

from hivegent.db.conversations.postgres import (
    PostgresConversationStore,
    _dump_message,
    _load_message,
)

_PROMPT = "The system prompt."


def _chain(*texts: str) -> list[ConversationEntry]:
    entries: list[ConversationEntry] = []

    for text in texts:
        entries.append(
            ConversationEntry(
                parent_id=entries[-1].id if entries else None,
                message=ModelRequest(
                    parts=[UserPromptPart(text)], instructions=_PROMPT
                ),
            )
        )

    return entries


def _stored(
    entries: Sequence[ConversationEntry],
) -> list[tuple[str, str | None, dict[str, Any]]]:
    return [
        (entry.id, entry.parent_id, _dump_message(entry.message, _PROMPT))
        for entry in entries
    ]


def test_a_history_is_cached_whole_or_not_at_all() -> None:
    """The cache keeps its budget, and a caller changing a read message leaves it as it was."""
    oldest, middle, head = _chain("a", "b", "c")
    stored = _stored([oldest, middle, head])
    store = PostgresConversationStore(
        cache_bytes=sum(len(to_json(payload)) for *_, payload in stored[1:])
    )
    store._keep(UUID(int=1), _PROMPT, [*stored, *_stored(_chain("d" * 1000))])
    history = store._cached_history(head.id, 2) or []

    assert history == [middle, head]
    history[-1].message.metadata = {"changed": True}
    assert store._cached_history(head.id, 2) == [middle, head]
    assert store._cached_history(head.id, None) is None


def test_a_request_stores_no_prompt_it_was_sent_under() -> None:
    """Only a prompt other than the conversation's is stored, and every request reads back as it was sent."""
    sent = ModelRequest(parts=[UserPromptPart("q")], instructions=_PROMPT)

    for request in (
        sent,
        replace(sent, instructions="Another."),
        replace(sent, instructions=None),
    ):
        payload = _dump_message(request, _PROMPT)

        assert ("instructions" in payload) == (request.instructions != _PROMPT)
        assert _load_message(payload, _PROMPT) == request
