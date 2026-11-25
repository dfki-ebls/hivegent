"""Tests for how :class:`PostgresConversationStore` stores, caches, and reads back messages."""

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlalchemy.dialects.postgresql import psycopg

from hivegent.db.conversations.postgres import (
    PostgresConversationStore,
    _fold,
    _path_query,
    _stored_entries,
)
from tests.helpers import chain


def _request(text: str, instructions: str | None) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(text)], instructions=instructions)


_ENTRIES = chain(
    _request("a", "p"),
    ModelResponse(parts=[TextPart("r")]),
    _request("b", "p"),
    _request("c", "q"),
    _request("d", None),
    _request("e", None),
)
_STORED = _stored_entries(_ENTRIES, {})


def test_a_request_stores_only_instructions_it_changes() -> None:
    """Every message reads back as it was sent, also past a response and back to none."""
    assert [message.changes for message in _STORED] == ["p", None, None, "q", "", None]
    assert [message.entry() for message in _STORED] == _ENTRIES


def test_any_window_of_a_path_reads_back_as_stored() -> None:
    """The rows the path query walks for a window, on up to the nearest change without their JSON, fold back into it."""
    for limit in (None, *range(1, len(_STORED) + 1)):
        start = 0 if limit is None else len(_STORED) - limit
        top = start

        while top > 0 and _STORED[top].changes is None:
            top -= 1

        rows = [
            (m.id, m.parent_id, m.changes, m.payload if i >= start else None, -i)
            for i, m in enumerate(_STORED[top:], start=top)
        ]

        assert _fold(rows) == _STORED[start:]
        assert _path_query(_STORED[-1].id, limit).compile(dialect=psycopg.dialect())


def test_a_cached_path_ends_at_the_first_message_it_misses() -> None:
    """The cache keeps its budget, so a read takes the newest messages from it and the rest from the database."""
    store = PostgresConversationStore(
        cache_bytes=sum(len(m.payload) + len(m.changes or "") for m in _STORED[2:])
    )
    store._keep(_STORED)

    assert store._cached_path(_STORED[-1].id, None) == (_STORED[:1:-1], _STORED[1].id)
    assert store._cached_path(_STORED[-1].id, 2) == (_STORED[:-3:-1], None)
