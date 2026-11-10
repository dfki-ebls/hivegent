"""Tests for the conversation and message ids a client chooses.

A client mints a conversation id and the id of every new user message, so
these pin what makes that safe: an absent id is claimed by one insert, only
once a request passed every check, another user's conversation answers exactly
like an id that is no UUID, and a taken message id is refused before the run.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

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
from tests.helpers import label_id, owned

_ID = UUID("0b6c4c9e-4f9a-4f0e-9d7a-3f6e2b1c8a90")
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
    ("owner", "opened"),
    [("alice", True), (None, True), ("bob", False)],
    ids=["owned", "absent", "foreign"],
)
async def test_only_an_owned_or_an_absent_id_opens(
    monkeypatch: pytest.MonkeyPatch, owner: str | None, opened: bool
) -> None:
    _ = _stub_session(monkeypatch, _Session(scalars_=[owner]))

    result = await db.open_conversation(_ALICE, _ID)

    assert result == ((owned(str(_ID), _ALICE), owner is not None) if opened else None)


@pytest.mark.parametrize(
    ("conversation_id", "opened"),
    [
        (str(_ID).upper(), True),
        ("V1StGXR8_Z5jdHi6B-myT", False),
        ("../" + str(_ID), False),
    ],
    ids=["uppercase", "other-form", "unsafe"],
)
async def test_a_path_id_opens_by_its_canonical_uuid(
    monkeypatch: pytest.MonkeyPatch, conversation_id: str, opened: bool
) -> None:
    """Another spelling of a UUID names its folder canonically, anything else answers 404."""
    _ = _stub_session(monkeypatch, _Session(scalars_=[None]))

    if not opened:
        with pytest.raises(HTTPException) as raised:
            await routes._opened_conversation(conversation_id, _ALICE)

        assert raised.value.status_code == 404
        return

    conversation, _ = await routes._opened_conversation(conversation_id, _ALICE)
    assert conversation.folder(Path("data")).name == str(_ID)


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
    assert set((lookup.params or {}).values()) == {label_id("c1"), _ID}


@pytest.mark.parametrize("claimed", [True, False], ids=["first-or-own", "taken"])
async def test_a_claim_skips_a_taken_id_and_answers_by_its_owner(
    monkeypatch: pytest.MonkeyPatch, claimed: bool
) -> None:
    """The insert never touches another user's row, a lost race only fails the check."""
    fake = _stub_session(monkeypatch, _Session(scalars_=[_ID if claimed else None]))

    assert await db.claim_conversation(owned(str(_ID), _ALICE)) is claimed

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
    assert await routes.get_conversation_messages((owned(str(_ID), _ALICE), False)) == []


async def test_another_users_id_answers_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The messages route included, so its URL shows as not found."""
    _ = _stub_session(monkeypatch, _Session(scalars_=["bob"]))

    with pytest.raises(HTTPException) as raised:
        await routes._opened_conversation(str(_ID), _ALICE)

    assert raised.value.status_code == 404


def _chat_request(message_id: str) -> Request:
    body = {
        "trigger": "submit-message",
        "id": str(_ID),
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
        (str(_ID), True, False, True, 409),
        (str(_ID), False, False, False, 404),
        (str(_ID), False, False, True, None),
        (str(_ID), False, True, False, None),
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
        await routes._run_chat(owned(str(_ID)), _chat_request(message_id), exists=exists)

    if isinstance(raised.value, HTTPException):
        assert raised.value.status_code == status

    claimed = not exists and status in {404, None}
    assert claims == ([owned(str(_ID))] if claimed else [])


async def test_a_turn_appends_under_the_clients_message_id_and_titles_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = Conversation(id=label_id("c1"), user_id="bob")
    fake = _stub_session(monkeypatch, _Session(scalars_=[row]))
    fork = label_id("fork")

    _ = await db.append_branch(
        owned("c1", _BOB),
        fork,
        [
            ModelRequest(parts=[UserPromptPart("hi")]),
            ModelResponse(parts=[TextPart("hello")]),
        ],
        prompt_id=_ID,
    )

    prompt, answer = fake.added
    assert isinstance(prompt, Message) and isinstance(answer, Message)
    assert (prompt.id, prompt.parent_id) == (_ID, fork)
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
