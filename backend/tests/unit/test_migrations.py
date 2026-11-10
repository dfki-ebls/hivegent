"""Tests for the data a migration rewrites, without a database."""

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

import hivegent
from hivegent.server.turns import pending_requests


def _migration(name: str) -> ModuleType:
    path = Path(hivegent.__file__).parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    response = ModelMessagesTypeAdapter.validate_python([payload])[0]
    requests = pending_requests([_PROMPT, response, *([_RAN] if ran_beside else [])])

    assert response_id == ("parent" if ran_beside else "leaf")
    assert requests is not None
    assert [call.tool_call_id for call in requests.approvals] == ["gate"]
    assert [call.tool_call_id for call in requests.calls] == ["ask"]
    assert requests.metadata == {"gate": {"value": "v"}}
