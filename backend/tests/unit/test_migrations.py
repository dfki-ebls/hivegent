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
from hivegent.agents.transcripts import (
    SubagentStep,
    SubagentTranscript,
    subagent_transcript,
)


def _migration(name: str) -> ModuleType:
    path = Path(hivegent.__file__).parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_STORE = "20261123_1200_65f6705faca2_conversation_store.py"


def _dump(message: ModelMessage) -> dict[str, Any]:
    return ModelMessagesTypeAdapter.dump_python([message], mode="json")[0]


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
    migration = _migration(_STORE)
    node = migration.Node
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

    record = migration.record(
        [
            node(prompt, None, _dump(_PROMPT)),
            node(old, prompt, _dump(ModelResponse(parts=[TextPart("old")]))),
            node(answer, prompt, _dump(ModelResponse(parts=[TextPart("new")]))),
            node(paused, answer, stored),
        ]
    )
    ids = {entry.old_id: entry for entry in record.entries}
    ui_ids = {
        entry.old_id: entry.payload["metadata"]["__pydantic_ai__"]["ui_message_id"]
        for entry in record.entries
    }

    assert record.head_id == ids[paused].id
    assert record.branches == [ids[old].id]
    assert ui_ids == {
        prompt: str(prompt),
        old: str(old),
        answer: str(answer),
        paused: str(answer),
    }
    assert record.deferred_tool_requests["approvals"][0]["tool_call_id"] == "gate"
    assert "deferredToolRequests" not in ids[paused].payload["metadata"]


def test_a_compaction_ahead_of_a_prompt_stays_in_the_reply_before() -> None:
    """It showed at the end of the reply before its prompt, so it keeps that reply's id."""
    migration = _migration(_STORE)
    node = migration.Node
    q1, a1, q2, a2, compaction = (UUID(int=i) for i in range(1, 6))

    record = migration.record(
        [
            node(q1, None, _dump(_PROMPT)),
            node(a1, q1, _dump(ModelResponse(parts=[TextPart("a1")]))),
            node(q2, compaction, _dump(_PROMPT)),
            node(a2, q2, _dump(ModelResponse(parts=[TextPart("a2")]))),
            node(compaction, a1, _dump(ModelResponse(parts=[CompactionPart("gist")]))),
        ]
    )
    ui_ids = {
        entry.old_id: entry.payload["metadata"]["__pydantic_ai__"]["ui_message_id"]
        for entry in record.entries
    }

    assert ui_ids[compaction] == str(a1)
    assert ui_ids[a2] == str(a2)


@pytest.mark.parametrize("through_prompt", [False, True])
def test_a_parent_cycle_is_refused(through_prompt: bool) -> None:
    """Also when a prompt in the cycle ends the walk to a UI message, and a leaf hangs off it."""
    migration = _migration(_STORE)
    one, two, leaf = (UUID(int=i) for i in range(1, 4))
    reply = _dump(ModelResponse(parts=[TextPart("a")]))
    first = _dump(_PROMPT) if through_prompt else reply

    with pytest.raises(ValueError, match="own ancestor"):
        migration.record(
            [
                migration.Node(one, two, first),
                migration.Node(two, one, reply),
                migration.Node(leaf, one, reply),
            ]
        )


def test_a_subagent_history_becomes_its_transcript() -> None:
    """The steps of the stored history replace it, as a turn now stores them."""
    migration = _migration(_STORE)
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
    [entry] = migration.record([migration.Node(UUID(int=1), None, stored)]).entries
    [request] = ModelMessagesTypeAdapter.validate_python([entry.payload])
    [part] = request.parts
    assert isinstance(part, ToolReturnPart)
    part.metadata = DataChunk.model_validate(part.metadata)

    assert subagent_transcript(part) == SubagentTranscript(
        transcript="subagent",
        steps=(
            SubagentStep(kind="reasoning"),
            SubagentStep(kind="tool", tool_name="grep"),
            SubagentStep(kind="message"),
        ),
    )


def test_the_first_prompt_moves_to_the_record() -> None:
    """A request under the root's prompt drops it, one under another prompt keeps its own."""
    migration = _migration(_STORE)
    root, reply, same, other = (UUID(int=i) for i in range(1, 5))
    nodes = [
        migration.Node(same, reply, _dump(replace(_PROMPT, instructions="p"))),
        migration.Node(other, reply, _dump(replace(_PROMPT, instructions="q"))),
        migration.Node(root, None, _dump(replace(_PROMPT, instructions="p"))),
        migration.Node(reply, root, _dump(ModelResponse(parts=[TextPart("a")]))),
    ]

    record = migration.record(nodes)
    kept = {entry.old_id: entry.payload.get("instructions") for entry in record.entries}

    assert record.instructions == "p"
    assert kept == {root: None, reply: None, same: None, other: "q"}
