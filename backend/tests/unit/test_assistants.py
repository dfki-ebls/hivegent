"""Assistants: what a user sees, what they may save, and how a run applies one."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents.common import UserDeps
from hivegent.agents.context import ASSISTANT_INSTRUCTIONS
from hivegent.agents.tools.assistant import ASSISTANT_NOTE, assistant_toolset
from hivegent.assistants import (
    delete_assistant,
    resolve_assistant,
    save_assistant,
    visible_assistants,
)
from hivegent.config import settings
from hivegent.store import User
from hivegent.types import AssistantSpec

USER = User("u", read_groups=frozenset({"archive"}), write_groups=frozenset({"team"}))


@pytest.fixture()
def operator_assistants(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An operator directory holding the global assistant ``hr``."""
    directory = data_dir / "operator-assistants"
    directory.mkdir()
    (directory / "hr").with_suffix(".json").write_text(
        AssistantSpec(instructions="Answer from the handbook.").model_dump_json()
    )
    monkeypatch.setattr(settings.assistants, "dir", directory)

    return directory


async def test_save_lists_and_deletes_by_scope(operator_assistants: Path) -> None:
    """A reference names one scope's assistant, which no other scope's name takes over."""
    await save_assistant(USER, "~", "brief", AssistantSpec(instructions="Mine."))
    await save_assistant(USER, "@team", "brief", AssistantSpec(instructions="Ours."))

    listed = [(e.item.ref, e.writable) for e in visible_assistants(USER)]
    assert listed == [("~/brief", True), ("@team/brief", True), ("/srv/hr", False)]
    assert (found := await resolve_assistant(USER, "~/brief")) is not None
    assert found.spec.instructions == "Mine."
    assert await resolve_assistant(USER, "brief") is None
    assert await resolve_assistant(USER, "@archive/brief") is None

    await delete_assistant(USER, "@team", "brief")
    assert await resolve_assistant(USER, "@team/brief") is None


@pytest.mark.parametrize(
    ("prefix", "name", "status"),
    [
        ("/srv", "x", 403),
        ("@archive", "x", 403),
        ("@other", "x", 403),
    ],
)
async def test_save_refuses_what_the_user_may_not_place(
    operator_assistants: Path, prefix: str, name: str, status: int
) -> None:
    with pytest.raises(HTTPException) as exc:
        _ = await save_assistant(USER, prefix, name, AssistantSpec(instructions="x"))

    assert exc.value.status_code == status


def _reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="ok")])


@pytest.mark.parametrize(("prefix", "fenced"), [("~", True), ("/srv", False)])
async def test_only_the_operators_instructions_are_instructions(
    operator_assistants: Path, prefix: str, fenced: bool
) -> None:
    """A user's assistant is a tool result, the operator's joins the instructions."""
    if prefix == "~":
        await save_assistant(USER, "~", "mine", AssistantSpec(instructions="Rhyme."))

    assistant = await resolve_assistant(USER, "~/mine" if fenced else "/srv/hr")
    agent = Agent(deps_type=UserDeps, toolsets=[assistant_toolset.toolset()])
    result = await agent.run(
        "hi",
        model=FunctionModel(_reply),
        deps=UserDeps(user=USER, mode="interactive", assistant=assistant),
        capabilities=[ASSISTANT_NOTE, ASSISTANT_INSTRUCTIONS],
    )
    reads = [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    instructions = "\n".join(
        message.instructions or ""
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
    )

    assert (len(reads) == 1 and "Rhyme." in str(reads[0])) == fenced
    assert ("Answer from the handbook." in instructions) == (not fenced)
