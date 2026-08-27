"""Tests that a real client turn is accepted wherever the browser sends one.

The protocol models forbid extra fields, so a part the AI SDK builds but
pydantic-ai has not transcribed rejects the whole request.  That happened twice
(``providerExecuted``/``title`` on ``DynamicToolUIPart``, then the ``id`` the
adapter itself streams on every reasoning part, fixed upstream in
pydantic-ai 2.34), and each time it took down every endpoint the browser hands
messages to: the chat route on an approval continuation and the import route on
an archive exported mid-session.  These pin the shape the client actually posts,
so the next such drift fails here rather than as a 422 in production.

Compaction used to be a third such endpoint; it now summarizes the persisted
active path and takes no messages from the browser at all.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ThinkingPart

from hivegent.server.vercel import ChatAdapter
from hivegent.types import ConversationArchive

# An assistant turn exactly as the SDK holds it after an OpenAI-compatible
# endpoint streamed reasoning and asked for approval, the case that 422'd: the
# `id` is the one the adapter's own `reasoning-start` chunk emitted.
_ASSISTANT_TURN: dict[str, Any] = {
    "id": "msg-1",
    "role": "assistant",
    "parts": [
        {"type": "step-start"},
        {"type": "reasoning", "id": "msg-1", "text": "they want a file", "state": "done"},
        {"type": "text", "text": "Writing it.", "state": "done"},
        {
            "type": "tool-write_document",
            "toolCallId": "call-1",
            "state": "approval-responded",
            "input": {"path": "~/notes.md"},
            "approval": {"id": "ap-1", "approved": True},
        },
    ],
}


def _via_chat(message: dict[str, Any]) -> list[ModelMessage]:
    body = {"trigger": "submit-message", "id": "c1", "messages": [message]}
    run_input = ChatAdapter.build_run_input(json.dumps(body).encode())
    return ChatAdapter.load_messages(run_input.messages)


def _via_import(message: dict[str, Any]) -> list[ModelMessage]:
    archive = ConversationArchive.model_validate(
        {"frontend": {"id": "c1", "messages": [message]}}
    )
    return ChatAdapter.load_messages(archive.active_path()[0])


_SURFACES = [_via_chat, _via_import]


@pytest.mark.parametrize("surface", _SURFACES, ids=lambda fn: fn.__name__)
def test_a_client_turn_is_accepted(
    surface: Callable[[dict[str, Any]], list[ModelMessage]],
) -> None:
    """The whole turn validates, reasoning part and all."""
    messages = surface(_ASSISTANT_TURN)

    thinking = [
        part
        for message in messages
        for part in message.parts
        if isinstance(part, ThinkingPart)
    ]
    assert [part.content for part in thinking] == ["they want a file"]


@pytest.mark.parametrize("surface", _SURFACES, ids=lambda fn: fn.__name__)
def test_a_coerced_approval_is_still_refused(
    surface: Callable[[dict[str, Any]], list[ModelMessage]],
) -> None:
    """Accepting the turn must not soften the gate on the client's approval.

    ``approved`` is deliberately strict: a value that merely coerces to ``True``
    fails the whole request rather than releasing a call that asked for a human
    decision, so no accommodation of client parts may relax it.
    """
    coerced = json.loads(json.dumps(_ASSISTANT_TURN).replace('"approved": true', '"approved": 1'))

    with pytest.raises(ValidationError):
        surface(coerced)
