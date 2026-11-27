"""Capability composition: guidance appears exactly when its tools do."""

from collections.abc import Callable, Sequence

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
from hivegent.agents.capabilities import FEATURES, build_capabilities
from hivegent.agents.common import UserDeps
from hivegent.agents.context import PERSONAL_INSTRUCTIONS
from hivegent.agents.tools.python import sandbox_surface
from hivegent.prompts import GROUNDING_INSTRUCTIONS, compose_instructions
from hivegent.store import User
from hivegent.types import Mode
from tests.helpers import in_run, owned, run_context

type Configure = Callable[..., None]

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


def _instructions() -> str:
    """Every static instruction the composed capabilities contribute."""
    return "\n".join(
        text
        for capability in build_capabilities(_deps())
        for text in _static_texts(capability.get_instructions())
    )


async def _params(
    *,
    relevant: frozenset[str] = frozenset(),
    mode: Mode = "interactive",
    extra: Sequence[Capability[UserDeps]] = (),
) -> ModelRequestParameters:
    """What the model is handed by the first request of a turn."""
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
    _ = await in_run(
        deps,
        chat_agent.run(
            "hi",
            model=FunctionModel(respond),
            deps=deps,
            capabilities=build_capabilities(deps, extra=extra),
        ),
    )

    return captured[0]


async def _tool_names(
    mode: Mode = "interactive", extra: Sequence[Capability[UserDeps]] = ()
) -> set[str]:
    """Every tool one run in *mode* would hand the model."""
    params = await _params(mode=mode, extra=extra)

    return {tool.name for tool in params.function_tools}


def test_grounding_rides_with_the_retrieval_tools(
    configure: Configure,
) -> None:
    assert GROUNDING_INSTRUCTIONS["en"] in _instructions()

    configure(tools={"disabled": sorted(_EXPLORE.tool_names)})

    assert GROUNDING_INSTRUCTIONS["en"] not in _instructions()
    # Path guidance is owned by explore and write, so write alone keeps it.
    assert "workspace-paths" in {c.id for c in build_capabilities(_deps())}


async def test_document_scope_rides_with_the_retrieval_tools(
    configure: Configure,
) -> None:
    """The note naming the user's selection is computed per request, so the static checks miss it."""
    selected = frozenset({"~/a.md"})
    scoped = await _params(relevant=selected)

    assert "~/a.md" in "\n".join(
        part.content for part in scoped.instruction_parts or []
    )

    configure(tools={"disabled": sorted(_EXPLORE.tool_names)})
    unscoped = await _params(relevant=selected)

    assert "~/a.md" not in "\n".join(
        part.content for part in unscoped.instruction_parts or []
    )


async def test_read_mode_writes_tmp_but_stages_nothing() -> None:
    names = await _tool_names("read")

    assert {"write_document", "edit_document"} <= names
    assert "apply_changes" not in names
    assert "apply_changes" in await _tool_names()


async def test_the_shape_specific_readers_are_named_where_the_model_looks() -> None:
    """A pointer to ``query_table`` is worth nothing when the model cannot see the name."""
    assert {"jq", "read_binary_document", "query_table"} <= await _tool_names()

    declared = sandbox_surface(run_context(_deps())).declarations
    assert "async def query_table(" in declared


async def test_an_operator_exclusion_never_reaches_the_model(
    configure: Configure,
) -> None:
    """It covers the tools an *extra* toolset brought too, an operator's only reach over them."""
    extra: FunctionToolset[UserDeps] = FunctionToolset()

    @extra.tool_plain(metadata={"effect": "read"})
    def remote_thing() -> str:
        """A tool an extra toolset brought."""
        return ""

    brought = [Capability(toolsets=[extra])]
    excluded = {"list_conversations", "get_conversation", "remote_thing"}

    assert excluded <= await _tool_names(extra=brought)

    configure(tools={"disabled": sorted(excluded)})

    assert not excluded & await _tool_names(extra=brought)


def test_a_misspelled_exclusion_is_a_startup_error(
    configure: Configure,
) -> None:
    capabilities.check_tool_settings()
    assert capabilities.unknown_tool_names(["delegate_task", "explore"]) == []

    configure(tools={"disabled": ["query_tables"]})

    with pytest.raises(ValueError, match="query_tables"):
        capabilities.check_tool_settings()


async def test_a_withheld_tool_is_called_through_no_surface(
    configure: Configure,
) -> None:
    """The debug console and MCP build the tools by the same rule a run does."""
    configure(tools={"disabled": ["delete_documents"]})

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
