"""Tests for the data a migration rewrites, without a database."""

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

import hivegent


def _migration(name: str) -> ModuleType:
    path = Path(hivegent.__file__).parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STORE = _migration("20261123_1200_65f6705faca2_conversation_store.py")
Node = STORE.Node


def _dump(message: ModelMessage) -> dict[str, Any]:
    return ModelMessagesTypeAdapter.dump_python([message], mode="json")[0]


def _ui_ids(record: Any) -> dict[UUID, str]:
    return {
        entry.old_id: entry.payload["metadata"]["__pydantic_ai__"]["ui_message_id"]
        for entry in record.entries
    }


_PROMPT = ModelRequest(parts=[UserPromptPart("q1")])
_PAUSED = ModelResponse(
    parts=[
        ToolCallPart("lookup", {}, "run"),
        ToolCallPart("write", {"value": "v"}, "gate"),
        ToolCallPart("ask_user", {}, "ask"),
    ],
    metadata={"approvalMetadata": {"gate": {"value": "v"}}},
)
_RAN = ModelRequest(parts=[ToolReturnPart("lookup", "found", "run")])
_ALONE = replace(_PAUSED, parts=_PAUSED.parts[1:])


@pytest.mark.parametrize("ran_beside", [False, True])
def test_a_paused_turn_gets_the_requests_it_awaits(ran_beside: bool) -> None:
    """The unanswered calls become the stored requests, approval and question apart."""
    paused = _migration(
        "20261101_1200_e3f4a5b6c7d8_uuid_ids_deferred_requests.py"
    ).paused
    leaf, parent = (_RAN, _PAUSED) if ran_beside else (_ALONE, _PROMPT)

    response_id, payload = paused("leaf", _dump(leaf), "parent", _dump(parent))
    requests = payload["metadata"]["deferredToolRequests"]

    assert response_id == ("parent" if ran_beside else "leaf")
    assert [call["tool_call_id"] for call in requests["approvals"]] == ["gate"]
    assert [call["tool_call_id"] for call in requests["calls"]] == ["ask"]
    assert requests["metadata"] == {"gate": {"value": "v"}}


def test_a_tree_becomes_a_store_record() -> None:
    """The newest leaf heads the record, UI ids ride on the messages, and the paused requests move to the record."""
    prompt, old, answer, paused = (UUID(int=i) for i in range(1, 5))
    stored = _dump(_ALONE)
    stored["metadata"] = {
        **stored["metadata"],
        "deferredToolRequests": {
            "calls": [],
            "approvals": [stored["parts"][0]],
            "metadata": {},
        },
    }

    record = STORE.record(
        [
            Node(prompt, None, _dump(_PROMPT)),
            Node(old, prompt, _dump(ModelResponse(parts=[TextPart("old")]))),
            Node(answer, prompt, _dump(ModelResponse(parts=[TextPart("new")]))),
            Node(paused, answer, stored),
        ]
    )
    ids = {entry.old_id: entry for entry in record.entries}

    assert record.head_id == ids[paused].id
    assert record.branches == [ids[old].id]
    assert _ui_ids(record) == {
        prompt: str(prompt),
        old: str(old),
        answer: str(answer),
        paused: str(answer),
    }
    assert record.deferred_tool_requests["approvals"][0]["tool_call_id"] == "gate"
    assert "deferredToolRequests" not in ids[paused].payload["metadata"]


def test_a_compaction_ahead_of_a_prompt_stays_in_the_reply_before() -> None:
    """It showed at the end of the reply before its prompt, so it keeps that reply's id."""
    q1, a1, q2, a2, compaction = (UUID(int=i) for i in range(1, 6))

    record = STORE.record(
        [
            Node(q1, None, _dump(_PROMPT)),
            Node(a1, q1, _dump(ModelResponse(parts=[TextPart("a1")]))),
            Node(q2, compaction, _dump(_PROMPT)),
            Node(a2, q2, _dump(ModelResponse(parts=[TextPart("a2")]))),
            Node(compaction, a1, _dump(ModelResponse(parts=[CompactionPart("gist")]))),
        ]
    )
    ui_ids = _ui_ids(record)

    assert ui_ids[compaction] == str(a1)
    assert ui_ids[a2] == str(a2)


@pytest.mark.parametrize("through_prompt", [False, True])
def test_a_parent_cycle_is_refused(through_prompt: bool) -> None:
    """Also when a prompt in the cycle ends the walk to a UI message, and a leaf hangs off it."""
    one, two, leaf = (UUID(int=i) for i in range(1, 4))
    reply = _dump(ModelResponse(parts=[TextPart("a")]))
    first = _dump(_PROMPT) if through_prompt else reply

    with pytest.raises(ValueError, match="own ancestor"):
        STORE.record(
            [
                Node(one, two, first),
                Node(two, one, reply),
                Node(leaf, one, reply),
            ]
        )


def test_a_subagent_history_becomes_its_transcript() -> None:
    """The steps of the stored history replace it, as a turn now stores them."""
    history = [
        _PROMPT,
        ModelResponse(parts=[ThinkingPart("t"), ToolCallPart("grep", {}, "g")]),
        ModelRequest(parts=[ToolReturnPart("grep", "hit", "g")]),
        ModelResponse(parts=[TextPart("found")]),
    ]
    stored = _dump(ModelRequest(parts=[ToolReturnPart("delegate_task", "found", "d1")]))
    stored["parts"][0]["metadata"] = {
        "type": "data-subagent-history",
        "id": "d1",
        "data": {"explore": ModelMessagesTypeAdapter.dump_python(history, mode="json")},
    }
    [entry] = STORE.record([Node(UUID(int=1), None, stored)]).entries
    [request] = ModelMessagesTypeAdapter.validate_python([entry.payload])
    [part] = request.parts
    assert isinstance(part, ToolReturnPart)

    assert DataChunk.model_validate(part.metadata).data == {
        "transcript": "subagent",
        "steps": [
            {"kind": "reasoning"},
            {"kind": "tool", "tool_name": "grep"},
            {"kind": "message"},
        ],
    }


def test_a_request_keeps_only_instructions_it_changes() -> None:
    """They leave every payload, and a request keeps them where they differ from its parent's, ``''`` for none."""
    root, reply, same, other, unset = (UUID(int=i) for i in range(1, 6))
    nodes = [
        Node(same, reply, _dump(replace(_PROMPT, instructions="p"))),
        Node(other, reply, _dump(replace(_PROMPT, instructions="q"))),
        Node(unset, other, _dump(_PROMPT)),
        Node(root, None, _dump(replace(_PROMPT, instructions="p"))),
        Node(reply, root, _dump(ModelResponse(parts=[TextPart("a")]))),
    ]

    entries = STORE.record(nodes).entries

    assert not any("instructions" in entry.payload for entry in entries)
    assert {entry.old_id: entry.instructions for entry in entries} == {
        root: "p",
        reply: None,
        same: None,
        other: "q",
        unset: "",
    }
