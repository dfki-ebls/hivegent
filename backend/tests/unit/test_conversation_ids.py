"""Tests for the conversation and message ids a client chooses.

A client mints a conversation id and the id of every new user message, so
these pin what makes that safe: an absent id is claimed by one insert, only
once a request passed every check, another user's conversation answers exactly
like an id that is no UUID, and a message id the history holds is refused
before the run.
"""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic_ai.conversation_store import InMemoryConversationStore
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from starlette.requests import Request

from hivegent.agents import RunPrefix
from hivegent.db import conversations as db
from hivegent.db.conversations import OwnedConversation, queries
from hivegent.server.routes.conversations import deps, history, turn
from hivegent.server.turns import stamp
from hivegent.store import User
from hivegent.tmp import tmp_dir
from hivegent.types import ChatRequest
from tests.helpers import owned

_ID = UUID("0b6c4c9e-4f9a-4f0e-9d7a-3f6e2b1c8a90")
_ALICE = User("alice")
_BOB = User("bob")


async def _claimed(user: User, *texts: str) -> OwnedConversation:
    """*user*'s conversation at :data:`_ID`, holding a prompt for each of *texts* under its text."""
    conversation = owned(str(_ID), user)
    loaded = await db.claim_conversation(conversation)
    assert loaded is not None

    if texts:
        prompts: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(text)]) for text in texts
        ]

        for prompt, text in zip(prompts, texts, strict=True):
            stamp([prompt], text)

        loaded.messages = prompts
        await queries.store.save(loaded, scope=user.id)

    return conversation


@pytest.mark.parametrize(
    ("owner", "opened"),
    [(_ALICE, True), (None, True), (_BOB, False)],
    ids=["owned", "absent", "foreign"],
)
async def test_only_an_owned_or_an_absent_id_opens(
    memory_store: InMemoryConversationStore, owner: User | None, opened: bool
) -> None:
    if owner is not None:
        await _claimed(owner)

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
    memory_store: InMemoryConversationStore, conversation_id: str, opened: bool
) -> None:
    """Another spelling of a UUID names its folder canonically, anything else answers 404."""
    if not opened:
        with pytest.raises(HTTPException) as raised:
            await deps._opened_conversation(conversation_id, _ALICE)

        assert raised.value.status_code == 404
        return

    conversation, _ = await deps._opened_conversation(conversation_id, _ALICE)
    assert conversation.folder(Path("data")).name == str(_ID)


async def test_a_claim_answers_by_its_owner(
    memory_store: InMemoryConversationStore,
) -> None:
    """The store's insert decides a race, and a lost one only fails the owner check."""
    assert await db.claim_conversation(owned(str(_ID), _ALICE)) is not None
    assert await db.claim_conversation(owned(str(_ID), _ALICE)) is not None
    assert await db.claim_conversation(owned(str(_ID), _BOB)) is None


async def test_an_absent_id_has_no_messages_yet(
    memory_store: InMemoryConversationStore,
) -> None:
    assert (
        await history.get_conversation_messages((owned(str(_ID), _ALICE), False)) == []
    )


async def test_another_users_id_answers_404(
    memory_store: InMemoryConversationStore,
) -> None:
    """The messages route included, so its URL shows as not found."""
    await _claimed(_BOB)

    with pytest.raises(HTTPException) as raised:
        await deps._opened_conversation(str(_ID), _ALICE)

    assert raised.value.status_code == 404


def _chat_request(message_id: str) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "id": str(_ID),
            "messages": [
                {
                    "id": message_id,
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                }
            ],
        }
    )


class _Reached(Exception):
    """Raised where the turn would start its run."""


_TAKEN = "1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b"


@pytest.mark.parametrize(
    ("message_id", "exists", "foreign", "status"),
    [
        ("msg-1", False, False, 422),
        (_TAKEN, True, False, 409),
        (str(_ID), False, True, 404),
        (str(_ID), False, False, None),
        (str(_ID), True, False, None),
    ],
    ids=["no-uuid", "message-taken", "conversation-taken", "free", "existing"],
)
async def test_a_turn_claims_its_id_only_once_every_check_passed(
    memory_store: InMemoryConversationStore,
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    run_prefix: RunPrefix,
    message_id: str,
    exists: bool,
    foreign: bool,
    status: int | None,
) -> None:
    """A message id the history holds is refused up front, and a refused request claims nothing.

    A refused request also leaves no `/tmp` for a conversation without a record.
    """

    def run(*_args: object, **_kwargs: object) -> None:
        raise _Reached

    if exists:
        await _claimed(User("u"), _TAKEN)

    if foreign:
        # Claimed by another user since the request opened the id.
        await _claimed(_BOB)

    async def build_run_prefix(*_args: object) -> RunPrefix:
        return run_prefix

    monkeypatch.setattr(turn, "build_run_prefix", build_run_prefix)
    monkeypatch.setattr(turn, "run_and_persist", run)

    with pytest.raises(HTTPException if status else _Reached) as raised:
        await turn.create_conversation_chat(
            (owned(str(_ID)), exists),
            User("u"),
            _chat_request(message_id),
            Request({"type": "http", "headers": []}),
            None,
        )

    if isinstance(raised.value, HTTPException):
        assert raised.value.status_code == status

    record = await memory_store.read_record(str(_ID))
    assert (record is not None) is (exists or foreign or status is None)
    assert tmp_dir(data_dir, owned(str(_ID))).exists() is (status is None)


async def test_the_store_refuses_a_call_naming_no_owner(
    memory_store: db.OwnedConversationStore,
) -> None:
    """pydantic-ai reads no scope as every owner's, so only the ownership check names none."""
    await _claimed(_ALICE)

    for call in (
        memory_store.load(str(_ID)),
        memory_store.list_conversations(),
        memory_store.delete(str(_ID)),
    ):
        with pytest.raises(UserError):
            await call

    assert await memory_store.owner_of(str(_ID)) == _ALICE.id
