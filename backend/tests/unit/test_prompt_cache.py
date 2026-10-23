"""The provider's cached prefix survives every turn of a conversation.

See "Run state appends its changes" in the backend README for why each request
must extend the previous one and add no user message mid-turn.
"""

import json
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

import httpx2
import pytest
from pydantic_ai import FunctionToolset, ModelRetry, models
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

import hivegent.llm as llm_module
from hivegent.agents.app import turn_usage_limits, user_agent
from hivegent.agents.capabilities import build_capabilities
from hivegent.agents.common import UserDeps
from hivegent.config import InferenceProvider
from hivegent.db.conversations import _dump_messages, _load_messages
from hivegent.llm import create_openai_chat_model
from hivegent.store import User
from hivegent.types import Mode, ToolsSpec
from tests.helpers import owned

type Payload = dict[str, Any]


def _completion(message: Payload) -> Payload:
    return {
        "id": "completion",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
                "message": {"role": "assistant", "content": None, **message},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _call_broken(index: int) -> Payload:
    return _completion(
        {
            "reasoning_content": f"Try the tool, attempt {index}.",
            "tool_calls": [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "broken", "arguments": "{}"},
                }
            ],
        }
    )


_ANSWER = _completion({"reasoning_content": "Done.", "content": "ok"})


def _rendered(messages: list[Payload]) -> list[Payload]:
    """*messages* as the chat template renders them.

    pydantic-ai sends an empty ``reasoning_content`` on the tool-calling steps
    after the last user message, which the template renders like a missing one.
    """
    return [
        {
            key: value
            for key, value in message.items()
            if not (key == "reasoning_content" and value == "")
        }
        for message in messages
    ]


def _users(request: Payload) -> int:
    return sum(message["role"] == "user" for message in request["messages"])


async def test_each_request_extends_the_previous_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)

    requests: list[Payload] = []
    replies: list[Payload] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))

        return httpx2.Response(200, json=replies.pop(0))

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(llm_module, "get_trusted_http_client", lambda: client)
    model = create_openai_chat_model(
        "qwen3.6",
        api_key="test",
        base_url="http://llm/v1",
        inference_provider=InferenceProvider.VLLM,
        base_url_is_trusted=True,
    )

    broken = FunctionToolset[UserDeps]()

    @broken.tool_plain(name="broken", retries=4)
    def _broken() -> str:
        raise ModelRetry("still broken")

    turns: list[int] = []

    async def turn(
        history: Sequence[ModelMessage],
        mode: Mode,
        relevant: frozenset[str],
        script: list[Payload],
        limits: UsageLimits = turn_usage_limits,
    ) -> list[ModelMessage]:
        turns.append(len(requests))
        replies.extend(script)
        result = await user_agent.run(
            "hi",
            model=model,
            deps=UserDeps(
                user=User("u"),
                mode=mode,
                conversation=owned("c1"),
                relevant_documents=relevant,
            ),
            capabilities=build_capabilities(
                ToolsSpec(), extra=[broken], mode=mode, language="en"
            ),
            instructions="chat instructions",
            message_history=list(history),
            usage_limits=limits,
        )
        assert not replies

        # Every turn starts from the stored conversation, as a chat turn does.
        return _load_messages(_dump_messages(result.all_messages()))

    # The memory is reported, and a tool call follows within the turn.
    history = await turn(
        (), "interactive", frozenset({"~/a.md"}), [_call_broken(-1), _ANSWER]
    )
    # The selection and the mode change, and a tool fails until its retry
    # note fires mid-turn.
    history = await turn(
        history,
        "write",
        frozenset({"~/b.md"}),
        [_call_broken(i) for i in range(4)] + [_ANSWER],
    )
    # The request budget runs low mid-turn.
    await turn(
        history,
        "write",
        frozenset({"~/b.md"}),
        [_call_broken(i) for i in range(4, 7)] + [_ANSWER],
        UsageLimits(request_limit=4),
    )

    # Each change did reach the model, or the checks below would hold vacuously.
    sent = json.dumps(requests[-1]["messages"])
    assert "~/b.md" in sent and "Write mode" in sent
    assert "failed 3 times in a row" in sent
    assert "most of its 4 model requests" in sent
    assert all(message["role"] != "system" for message in requests[-1]["messages"][1:])

    for previous, current in pairwise(requests):
        assert current["tools"] == previous["tools"]
        prefix = _rendered(current["messages"])[: len(previous["messages"])]
        assert prefix == _rendered(previous["messages"])

    # Only a turn's first request may carry a new user message.
    for index in set(range(1, len(requests))) - set(turns):
        assert _users(requests[index]) == _users(requests[index - 1]), index
