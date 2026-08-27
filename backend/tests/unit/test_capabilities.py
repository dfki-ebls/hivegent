"""Capability composition: guidance appears exactly when its tools do."""

from collections.abc import Sequence

import pytest
from pydantic_ai import FunctionToolset
from pydantic_ai.agent import AgentInstructions
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents import capabilities
from hivegent.agents.app import user_agent
from hivegent.agents.capabilities import (
    FEATURES,
    build_capabilities,
    collect_tool_schemas,
)
from hivegent.agents.common import UserDeps, scope_instructions
from hivegent.auth import User
from hivegent.prompts import GROUNDING_INSTRUCTIONS
from hivegent.server.routes.meta import list_tools
from hivegent.store import Casebase
from hivegent.types import ToolsSpec

_EXPLORE = next(feature for feature in FEATURES if feature.id == "explore")


def _static_texts(instructions: AgentInstructions[UserDeps]) -> list[str]:
    """The literal blocks in *instructions*, skipping the callables."""
    if isinstance(instructions, str):
        return [instructions]

    if isinstance(instructions, Sequence):
        return [part for part in instructions if isinstance(part, str)]

    return []


def _dynamic_parts(instructions: AgentInstructions[UserDeps]) -> list[object]:
    """The callables in *instructions*, skipping the literal blocks."""
    if isinstance(instructions, str):
        return []

    if isinstance(instructions, Sequence):
        return [part for part in instructions if callable(part)]

    return [instructions] if callable(instructions) else []


def _callables(spec: ToolsSpec) -> list[object]:
    """Every dynamic instruction the composed capabilities contribute."""
    return [
        part
        for capability in build_capabilities(spec, mode="interactive")
        for part in _dynamic_parts(capability.get_instructions() or [])
    ]


def _instructions(spec: ToolsSpec) -> str:
    """Every static instruction the composed capabilities contribute."""
    return "\n".join(
        text
        for capability in build_capabilities(spec, mode="interactive")
        for text in _static_texts(capability.get_instructions())
    )


def test_grounding_rides_with_the_retrieval_tools() -> None:
    """Disabling every explore tool must retract the search-first mandate."""
    assert GROUNDING_INSTRUCTIONS in _instructions(ToolsSpec())

    disabled = ToolsSpec(disabled_tools=sorted(_EXPLORE.tool_names))
    assert GROUNDING_INSTRUCTIONS not in _instructions(disabled)


def test_shared_block_survives_on_its_remaining_feature() -> None:
    """Path guidance is owned by explore *and* write, so write alone keeps it."""
    disabled = ToolsSpec(disabled_tools=sorted(_EXPLORE.tool_names))
    ids = {
        capability.id for capability in build_capabilities(disabled, mode="interactive")
    }
    assert "workspace-paths" in ids


def test_document_scope_rides_with_the_retrieval_tools() -> None:
    """The block naming the user's selection must reach the model.

    It is the only thing that ties a bare "these documents" to the files the
    eye toggle picked, and being a callable it is invisible to the static
    checks above, so it is asserted on its own.
    """
    assert scope_instructions in _callables(ToolsSpec())

    disabled = ToolsSpec(disabled_tools=sorted(_EXPLORE.tool_names))
    assert scope_instructions not in _callables(disabled)


def _request_params(
    monkeypatch: pytest.MonkeyPatch, *, relevant: frozenset[str]
) -> ModelRequestParameters:
    """Run one turn against a stub model and capture what it was handed."""

    async def _memory(_user_id: str) -> str:
        return "remembered"

    monkeypatch.setattr(capabilities, "load_memory", _memory)
    captured: list[ModelRequestParameters] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(info.model_request_parameters)
        return ModelResponse(parts=[TextPart("ok")])

    deps = UserDeps(
        user_id="u",
        store=Casebase(kind="user", id="u"),
        mode="interactive",
        relevant_documents=relevant,
    )
    user_agent.run_sync(
        "hi",
        model=FunctionModel(respond),
        deps=deps,
        capabilities=build_capabilities(ToolsSpec(), mode="interactive"),
    )
    return captured[0]


def test_the_cacheable_prefix_survives_a_changed_document_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-turn guidance must land behind every block that does not change.

    The document scope and the stored memory are resolved per turn, so a
    provider re-reads the whole prompt from wherever the first of them sits.
    They are composed with the feature they explain, and only pydantic-ai's
    hoisting of dynamic instruction parts keeps them at the end; this pins that,
    since losing it would silently move the cache boundary to the top of the
    prompt without changing a single visible byte.
    """

    def static_part(relevant: frozenset[str]) -> str:
        parts = _request_params(monkeypatch, relevant=relevant).instruction_parts or []
        return next(part.content for part in parts if not part.dynamic)

    assert static_part(frozenset({"~/a.md"})) == static_part(frozenset({"~/b.md"}))


def _tool_names(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Every tool one interactive run would hand the model."""
    params = _request_params(monkeypatch, relevant=frozenset())
    return {tool.name for tool in params.function_tools}


def test_the_shape_specific_readers_are_registered_not_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pointer is worth nothing when its target is not in the tool list.

    ``query_table`` and ``read_binary_document`` were withheld until tool search
    asked for them, on the reasoning that ``read_document`` names them on the
    refusal that needs them.  A model that cannot see the name reads that
    pointer as describing a tool it was not given: one run answered the pointer
    at ``query_table`` by probing for ``pandas``, then hand-parsing the markdown
    projection.  The pointer stands and the deferral is gone.
    """
    monkeypatch.setattr(capabilities.settings.tools, "excluded", [])

    assert {"query_table", "jq", "read_binary_document"} <= _tool_names(monkeypatch)


def test_the_default_exclusions_never_reach_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What deferral used to decide silently is now an operator's list."""
    excluded = set(capabilities.settings.tools.excluded)
    assert excluded == {"list_conversations", "get_conversation"}

    assert not excluded & _tool_names(monkeypatch)


def test_an_operator_exclusion_retracts_the_feature_it_empties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One namespace, one mechanism: it drops instructions as a user's does."""
    monkeypatch.setattr(
        capabilities.settings.tools, "excluded", sorted(_EXPLORE.tool_names)
    )

    assert GROUNDING_INSTRUCTIONS not in _instructions(ToolsSpec())


def test_an_exclusion_reaches_the_tools_an_mcp_server_brought(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one filter covers *extra* too, an operator's only reach over them."""
    extra: FunctionToolset[UserDeps] = FunctionToolset()

    @extra.tool_plain
    def remote_thing() -> str:
        """A tool a user-configured MCP server brought."""
        return ""

    def names() -> set[str]:
        captured: list[ModelRequestParameters] = []

        def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            captured.append(info.model_request_parameters)
            return ModelResponse(parts=[TextPart("ok")])

        async def _memory(_user_id: str) -> str:
            return ""

        monkeypatch.setattr(capabilities, "load_memory", _memory)
        user_agent.run_sync(
            "hi",
            model=FunctionModel(respond),
            deps=UserDeps(
                user_id="u", store=Casebase(kind="user", id="u"), mode="interactive"
            ),
            capabilities=build_capabilities(
                ToolsSpec(), extra=[extra], mode="interactive"
            ),
        )
        return {tool.name for tool in captured[0].function_tools}

    assert "remote_thing" in names()

    monkeypatch.setattr(capabilities.settings.tools, "excluded", ["remote_thing"])
    assert "remote_thing" not in names()


async def test_an_excluded_tool_offers_the_user_no_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settings dialog renders a checkbox per entry of this listing.

    A tool the deployment withholds must not appear there switched on, so the
    user-facing listing drops the exclusions while the admin console, which is
    where the name to exclude is read, keeps them.
    """
    monkeypatch.setattr(capabilities.settings.tools, "excluded", ["query_table"])

    assert "query_table" in {tool.name for tool in collect_tool_schemas()}
    assert "query_table" not in {tool.name for tool in await list_tools(User(id="u"))}


def test_a_misspelled_exclusion_is_a_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It would withhold nothing, and the only symptom is a schema still there."""
    capabilities.check_excluded_tools()

    monkeypatch.setattr(capabilities.settings.tools, "excluded", ["query_tables"])
    with pytest.raises(ValueError, match="query_tables"):
        capabilities.check_excluded_tools()
