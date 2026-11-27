"""The title the aux model writes once a new conversation's first turn ended."""

from collections.abc import Sequence
from functools import partial
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
    prompt: str | list[BinaryContent],
    claimed: str | None,
    read: list[str],
) -> None:
    generated: list[list[str]] = []
    set_calls: list[tuple[str, str | None]] = []

    async def generate(messages: Sequence[ModelMessage], _llm: LlmConfig) -> str:
        generated.append(list(islice(titles._texts(messages), 4)))

        return "Bee dances"

    async def set_title(
        _conversation: OwnedConversation, title: str, *, expected_title: str | None
    ) -> None:
        set_calls.append((title, expected_title))

    saved = SavedTurn()
    llm = LlmConfig(model="small")
    title = partial(
        titles.title_first_turn,
        owned("c"),
        saved,
        llm,
        claimed,
        generate=generate,
        set_title=set_title,
    )

    # Nothing is titled before the turn is stored.
    await title()
    assert generated == []

    saved.messages = [ModelRequest(parts=[UserPromptPart(content=prompt)]), _ANSWER]
    await title()

    assert generated == [[*read, "With a waggle dance."]]
    assert set_calls == [("Bee dances", claimed)]
