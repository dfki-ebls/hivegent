"""Tests for the conversation and message ids a client chooses.

A client mints a conversation id and the id of every new user message, so
these pin what makes that safe: an absent id is claimed by one insert, only
once a request passed every check, another user's conversation answers exactly
like an invalid id, and a taken message id is refused before the run.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import NoResultFound
from sqlalchemy.sql import ClauseElement
from starlette.requests import Request

from hivegent.agents import RunPrefix
from hivegent.db import conversations as db
from hivegent.db.conversations import OwnedConversation
from hivegent.db.models import Conversation, Message
from hivegent.server.routes import conversations as routes
from hivegent.store import User
from tests.helpers import conversation_id, owned

_ID = "0b6c4c9e-4f9a-4f0e-9d7a-3f6e2b1c8a90"
_OTHER_FORM = "V1StGXR8_Z5jdHi6B-myT"
_ALICE = User("alice")
_BOB = User("bob")


@dataclass(slots=True)
class _Result:
    value: object

    def one(self) -> object:
        if self.value is None:
            raise NoResultFound

        return self.value


@dataclass(slots=True)
class _Session:
    """A transaction answering *scalars* in turn and keeping its writes."""

    scalars_: list[object] = field(default_factory=list)
    executed: list[ClauseElement] = field(default_factory=list)
    added: list[object] = field(default_factory=list)

    async def execute(self, statement: ClauseElement) -> None:
        self.executed.append(statement)

    async def scalar(self, statement: ClauseElement) -> object:
        self.executed.append(statement)

        return self.scalars_.pop(0)

    async def scalars(self, _statement: object) -> _Result:
        return _Result(self.scalars_.pop(0))

    def add(self, row: object) -> None:
        self.added.append(row)


def _stub_session(monkeypatch: pytest.MonkeyPatch, fake: _Session) -> _Session:
    @asynccontextmanager
    async def session() -> AsyncGenerator[_Session]:
        yield fake

    monkeypatch.setattr(db, "session", session)

    return fake


@pytest.mark.parametrize(
    ("owner", "conversation_id", "opened"),
    [
        ("alice", _ID, True),
        (None, _ID, True),
        ("bob", _ID, False),
        (None, _OTHER_FORM, False),
        (None, _ID.upper(), False),
        (None, "../" + _ID, False),
    ],
    ids=["owned", "absent", "foreign", "absent-other-form", "uppercase", "unsafe"],
)
async def test_only_an_owned_or_an_absent_canonical_id_opens(
    monkeypatch: pytest.MonkeyPatch,
    owner: str | None,
    conversation_id: str,
    opened: bool,
) -> None:
    """Another user's id answers like an invalid one."""
    _ = _stub_session(monkeypatch, _Session(scalars_=[owner]))

    result = await db.open_conversation(_ALICE, conversation_id)

    assert result == (
        (owned(conversation_id, _ALICE), owner is not None) if opened else None
    )


async def test_a_taken_prompt_id_is_looked_up_in_its_own_conversation_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Node ids are keyed per conversation, so another conversation's ids are never probed."""
    fake = _stub_session(monkeypatch, _Session(scalars_=[True]))

    with pytest.raises(db.MessageIdTaken):
        _ = await db.resolve_fork(
            owned("c1", _ALICE), regenerate=False, message_id=None, prompt_id=_ID
        )

    lookup = fake.executed[0].compile(dialect=postgresql.dialect())
    assert "messages.conversation_id" in str(lookup)
    assert set((lookup.params or {}).values()) == {conversation_id("c1"), _ID}


@pytest.mark.parametrize("claimed", [True, False], ids=["first-or-own", "taken"])
async def test_a_claim_skips_a_taken_id_and_answers_by_its_owner(
    monkeypatch: pytest.MonkeyPatch, claimed: bool
) -> None:
    """The insert never touches another user's row, a lost race only fails the check."""
    fake = _stub_session(monkeypatch, _Session(scalars_=[_ID if claimed else None]))

    assert await db.claim_conversation(owned(_ID, _ALICE)) is claimed

    insert = next(
        compiled
        for statement in fake.executed
        if "INSERT INTO conversations"
        in str(compiled := statement.compile(dialect=postgresql.dialect()))
    )
    assert "ON CONFLICT DO NOTHING" in str(insert)
    params = insert.params or {}
    assert (params["id"], params["user_id"]) == (_ID, "alice")


async def test_an_absent_id_has_no_messages_yet() -> None:
    assert await routes.get_conversation_messages((owned(_ID, _ALICE), False)) == []


async def test_another_users_id_answers_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The messages route included, so its URL shows as not found."""
    _ = _stub_session(monkeypatch, _Session(scalars_=["bob"]))

    with pytest.raises(HTTPException) as raised:
        await routes._opened_conversation(_ID, _ALICE)

    assert raised.value.status_code == 404


def _chat_request(message_id: str) -> Request:
    body = {
        "trigger": "submit-message",
        "id": _ID,
        "messages": [
            {
                "id": message_id,
                "role": "user",
                "parts": [{"type": "text", "text": "hi"}],
            }
        ],
    }
    chunks = [{"type": "http.request", "body": json.dumps(body).encode()}]

    async def receive() -> dict[str, Any]:
        return chunks.pop()

    headers = [(b"content-type", b"application/json")]

    return Request({"type": "http", "method": "POST", "headers": headers}, receive)


class _Reached(Exception):
    """Raised where the turn would start its run."""


@pytest.mark.parametrize(
    ("message_id", "taken", "exists", "claimable", "status"),
    [
        ("msg-1", False, False, True, 422),
        (_ID, True, False, True, 409),
        (_ID, False, False, False, 404),
        (_ID, False, False, True, None),
        (_ID, False, True, False, None),
    ],
    ids=["no-uuid", "message-taken", "conversation-taken", "free", "existing"],
)
async def test_a_turn_claims_its_id_only_once_every_check_passed(
    monkeypatch: pytest.MonkeyPatch,
    run_prefix: RunPrefix,
    message_id: str,
    taken: bool,
    exists: bool,
    claimable: bool,
    status: int | None,
) -> None:
    """A taken message id would lose the turn at its write, so it is refused up front."""
    claims: list[OwnedConversation] = []

    async def history(*_args: object, **_kwargs: object) -> tuple[list[Any], None]:
        if taken:
            raise db.MessageIdTaken

        return [], None

    async def claim(conversation: OwnedConversation) -> bool:
        claims.append(conversation)

        return claimable

    async def run(*_args: object, **_kwargs: object) -> None:
        raise _Reached

    monkeypatch.setattr(routes, "build_run_prefix", lambda *_args: run_prefix)
    monkeypatch.setattr(routes, "resolve_fork", history)
    monkeypatch.setattr(routes, "claim_conversation", claim)
    monkeypatch.setattr(routes, "run_and_persist", run)

    with pytest.raises(HTTPException if status else _Reached) as raised:
        await routes._run_chat(owned(_ID), _chat_request(message_id), exists=exists)

    if isinstance(raised.value, HTTPException):
        assert raised.value.status_code == status

    claimed = not exists and status in {404, None}
    assert claims == ([owned(_ID)] if claimed else [])


async def test_a_turn_appends_under_the_clients_message_id_and_titles_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = Conversation(id="c1", user_id="bob")
    fake = _stub_session(monkeypatch, _Session(scalars_=[row]))

    _ = await db.append_branch(
        owned("c1", _BOB),
        "fork",
        [
            ModelRequest(parts=[UserPromptPart("hi")]),
            ModelResponse(parts=[TextPart("hello")]),
        ],
        prompt_id=_ID,
    )

    prompt, answer = fake.added
    assert isinstance(prompt, Message) and isinstance(answer, Message)
    assert (prompt.id, prompt.parent_id) == (_ID, "fork")
    assert answer.parent_id == _ID and answer.id != _ID
    assert row.title == "hi"


async def test_a_turn_never_creates_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A conversation its user does not own, claimed or not, takes no write."""
    fake = _stub_session(monkeypatch, _Session(scalars_=[None]))

    with pytest.raises(NoResultFound):
        await db.append_branch(
            owned("c1", _BOB), None, [ModelRequest(parts=[UserPromptPart("hi")])]
        )

    assert fake.added == []
