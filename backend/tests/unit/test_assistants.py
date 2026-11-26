"""Assistants: what a user sees, what they may save, and how a run applies one."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic_ai import Agent
from pydantic_ai.messages import (
    InstructionDeltaPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents.common import UserDeps
from hivegent.agents.context import assistant_instructions
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


@pytest.mark.parametrize(
    ("ref", "authority"), [("~/mine", "user"), ("/srv/hr", "operator")]
)
async def test_an_assistants_instructions_carry_its_authors_authority(
    operator_assistants: Path, ref: str, authority: str
) -> None:
    """A user's assistant reaches the model as the user's, the operator's joins the instructions."""
    await save_assistant(USER, "~", "mine", AssistantSpec(instructions="Rhyme."))
    assistant = await resolve_assistant(USER, ref)
    deps = UserDeps(user=USER, mode="interactive", assistant=assistant)
    result = await Agent(deps_type=UserDeps).run(
        "hi",
        model=FunctionModel(_reply),
        deps=deps,
        capabilities=[assistant_instructions(deps)],
    )
    request = result.all_messages()[0]
    assert isinstance(request, ModelRequest)
    lowered = [part for part in request.parts if isinstance(part, InstructionDeltaPart)]

    assert [part.authority for part in lowered] == (
        [] if authority == "operator" else ["user"]
    )
    assert ("Answer from the handbook." in (request.instructions or "")) == (
        authority == "operator"
    )
