"""Capability composition: guidance appears exactly when its tools do."""

import asyncio
from collections.abc import Sequence

import pytest
from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.agent import AgentInstructions
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    InstructionDeltaPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents import capabilities
from hivegent.agents.app import chat_agent
from hivegent.agents.capabilities import (
    FEATURES,
    build_capabilities,
)
from hivegent.agents.common import UserDeps
from hivegent.agents.context import PERSONAL_INSTRUCTIONS
from hivegent.agents.tools.python import sandbox_surface
from hivegent.prompts import GROUNDING_INSTRUCTIONS, compose_instructions
from hivegent.store import User
from hivegent.types import Mode
from tests.helpers import in_run, owned, run_context

_EXPLORE = next(feature for feature in FEATURES if feature.id == "explore")


def _static_texts(instructions: AgentInstructions[UserDeps]) -> list[str]:
    """The literal blocks in *instructions*, skipping the callables."""
    if isinstance(instructions, str):
        return [instructions]

    if isinstance(instructions, Sequence):
        return [part for part in instructions if isinstance(part, str)]

    return []


def _deps(mode: Mode = "interactive") -> UserDeps:
    return UserDeps(user=User("u"), mode=mode)


def _withhold(monkeypatch: pytest.MonkeyPatch, names: Sequence[str]) -> None:
    """Have the operator withhold *names*."""
    monkeypatch.setattr(capabilities.settings.tools, "disabled", list(names))


def _instructions() -> str:
    """Every static instruction the composed capabilities contribute."""
    return "\n".join(
        text
        for capability in build_capabilities(_deps())
        for text in _static_texts(capability.get_instructions())
    )


def test_grounding_rides_with_the_retrieval_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Withholding every explore tool must retract the search-first mandate."""
    assert GROUNDING_INSTRUCTIONS["en"] in _instructions()

    _withhold(monkeypatch, sorted(_EXPLORE.tool_names))
    assert GROUNDING_INSTRUCTIONS["en"] not in _instructions()


def test_shared_block_survives_on_its_remaining_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path guidance is owned by explore *and* write, so write alone keeps it."""
    _withhold(monkeypatch, sorted(_EXPLORE.tool_names))
    ids = {capability.id for capability in build_capabilities(_deps())}
    assert "workspace-paths" in ids


def _turn(
    *,
    relevant: frozenset[str] = frozenset(),
    mode: Mode = "interactive",
) -> ModelRequestParameters:
    """Run one turn against a stub model, returning what it was first handed."""
    captured: list[ModelRequestParameters] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(info.model_request_parameters)
        return ModelResponse(parts=[TextPart("ok")])

    deps = UserDeps(
        user=User("u"),
        mode=mode,
        conversation=owned("c1"),
        relevant_documents=relevant,
    )
    _ = asyncio.run(
        in_run(
            deps,
            chat_agent.run(
                "hi",
                model=FunctionModel(respond),
                deps=deps,
                capabilities=build_capabilities(deps),
            ),
        )
    )
    return captured[0]


def _instruction_text(params: ModelRequestParameters) -> str:
    return "\n".join(part.content for part in params.instruction_parts or [])


def test_document_scope_rides_with_the_retrieval_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The note naming the user's selection must reach the model.

    It is the only thing that ties a bare "these documents" to the files the
    eye toggle picked, and being computed per request it is invisible to the
    static checks above, so it is asserted on its own.
    """
    selected = frozenset({"~/a.md"})
    assert "~/a.md" in _instruction_text(_turn(relevant=selected))

    _withhold(monkeypatch, sorted(_EXPLORE.tool_names))
    assert "~/a.md" not in _instruction_text(_turn(relevant=selected))


def _tool_names(mode: Mode = "interactive") -> set[str]:
    """Every tool one run in *mode* would hand the model."""
    params = _turn(mode=mode)

    return {tool.name for tool in params.function_tools}


def test_read_mode_writes_tmp_but_stages_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/tmp` is writable in every mode, so the write tools stay, but nothing stages."""
    monkeypatch.setattr(capabilities.settings.tools, "disabled", [])
    names = _tool_names("read")

    assert {"write_document", "edit_document"} <= names
    assert "apply_changes" not in names
    assert "apply_changes" in _tool_names()


def test_the_shape_specific_readers_are_never_merely_hinted_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pointer is worth nothing when its target is named nowhere the model looks.

    ``query_table`` and ``read_binary_document`` were once withheld until tool
    search asked for them, on the reasoning that ``read_document`` names them on
    the refusal that needs them.  A model that cannot see the name reads that
    pointer as describing a tool it was not given: one run answered the pointer
    at ``query_table`` by probing for ``pandas``, then hand-parsing the markdown
    projection.

    What that cost was the name, not the schema, so this asks where the name
    appears rather than which surface carries it.  A tool on neither is back to
    being deferred under another word.

    A tool a pointer names is therefore never moved to the sandbox alone, since
    the pointer has to reach a name the model can call directly.  Being on both
    surfaces is not a contradiction: the turn that only wants the answer makes
    the call, and the program that has already loaded the file does not spend a
    turn to query it.
    """
    monkeypatch.setattr(capabilities.settings.tools, "disabled", [])
    names = _tool_names()

    assert {"jq", "read_binary_document", "query_table"} <= names

    declared = sandbox_surface(
        run_context(UserDeps(user=User("u"), mode="interactive"))
    ).declarations
    assert "async def query_table(" in declared


def test_an_operator_exclusion_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What deferral used to decide silently is now an operator's list."""
    excluded = {"list_conversations", "get_conversation", "search_conversation"}
    monkeypatch.setattr(capabilities.settings.tools, "disabled", sorted(excluded))

    assert not excluded & _tool_names()


def test_an_exclusion_reaches_the_tools_an_mcp_server_brought(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one filter covers *extra* too, an operator's only reach over them."""
    extra: FunctionToolset[UserDeps] = FunctionToolset()

    @extra.tool_plain(metadata={"effect": "read"})
    def remote_thing() -> str:
        """A tool an extra toolset brought."""
        return ""

    def names() -> set[str]:
        captured: list[ModelRequestParameters] = []

        def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            captured.append(info.model_request_parameters)
            return ModelResponse(parts=[TextPart("ok")])

        _ = asyncio.run(
            in_run(
                _deps(),
                chat_agent.run(
                    "hi",
                    model=FunctionModel(respond),
                    deps=_deps(),
                    capabilities=build_capabilities(
                        _deps(), extra=[Capability(toolsets=[extra])]
                    ),
                ),
            )
        )
        return {tool.name for tool in captured[0].function_tools}

    assert "remote_thing" in names()

    monkeypatch.setattr(capabilities.settings.tools, "disabled", ["remote_thing"])
    assert "remote_thing" not in names()


def test_a_misspelled_exclusion_is_a_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It would withhold nothing, and the only symptom is a schema still there."""
    capabilities.check_tool_settings()

    monkeypatch.setattr(capabilities.settings.tools, "disabled", ["query_tables"])
    with pytest.raises(ValueError, match="query_tables"):
        capabilities.check_tool_settings()


def test_delegation_is_known() -> None:
    """The operator may withhold the delegation tools and the subagents."""
    assert capabilities.unknown_tool_names(["delegate_task", "explore"]) == []


async def test_a_withheld_tool_is_called_through_no_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The debug console and MCP build the tools by the same rule a run does."""
    _withhold(monkeypatch, ["delete_documents"])

    with pytest.raises(KeyError):
        await capabilities.invoke_agent_tool(
            "delete_documents", {"file_paths": ["~/a.md"]}, _deps()
        )


async def test_the_users_instructions_reach_the_model_as_theirs() -> None:
    """They never join the system prompt, which only the operator's instructions do."""

    def reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    deps = UserDeps(user=User("u"), mode="interactive", instructions="Rhyme.")
    result = await Agent(deps_type=UserDeps).run(
        "hi",
        model=FunctionModel(reply),
        deps=deps,
        instructions=compose_instructions("en", "Cite the handbook."),
        capabilities=[PERSONAL_INSTRUCTIONS],
    )
    request = result.all_messages()[0]
    assert isinstance(request, ModelRequest)
    lowered = [part for part in request.parts if isinstance(part, InstructionDeltaPart)]

    assert [part.authority for part in lowered] == ["user"]
    assert "Rhyme." not in (request.instructions or "")
    assert "Cite the handbook." in (request.instructions or "")
