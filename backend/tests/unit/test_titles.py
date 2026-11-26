"""The title the aux model writes once a new conversation's first turn ended."""

from collections.abc import Sequence
from itertools import islice

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from hivegent.db.conversations import OwnedConversation
from hivegent.llm_config import LlmConfig
from hivegent.server.routes.conversations import titles
from hivegent.server.routes.conversations.compaction import SavedTurn
from tests.helpers import owned

_ANSWER = ModelResponse(parts=[TextPart(content="With a waggle dance.")])
_IMAGE = BinaryContent(b"\x89PNG", media_type="image/png")


@pytest.mark.parametrize(
    ("prompt", "claimed", "read"),
    [
        ("How do bees dance?", "How do bees dance?", ["How do bees dance?"]),
        ([_IMAGE], None, []),
    ],
    ids=["text", "attachment-only"],
)
async def test_a_stored_first_turn_is_titled_from_its_prompt_and_answer(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str | list[BinaryContent],
    claimed: str | None,
    read: list[str],
) -> None:
    written: list[tuple[str, str | None, list[str]]] = []

    async def generate(messages: Sequence[ModelMessage], _llm: LlmConfig) -> str:
        texts = list(islice(titles._texts(messages), 4))
        written.append(("Bee dances", claimed, texts))

        return "Bee dances"

    async def set_title(
        _conversation: OwnedConversation, title: str, *, expected_title: str | None
    ) -> None:
        assert written[-1][:2] == (title, expected_title)

    monkeypatch.setattr(titles, "_generate_title", generate)
    monkeypatch.setattr(titles, "set_conversation_title", set_title)
    saved = SavedTurn()
    llm = LlmConfig(model="small")

    # Nothing is titled before the turn is stored.
    await titles.title_first_turn(owned("c"), saved, llm, claimed)
    saved.messages = [ModelRequest(parts=[UserPromptPart(content=prompt)]), _ANSWER]
    await titles.title_first_turn(owned("c"), saved, llm, claimed)

    assert written == [("Bee dances", claimed, [*read, "With a waggle dance."])]
