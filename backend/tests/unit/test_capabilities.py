"""Capability composition: guidance appears exactly when its tools do."""

import asyncio
from collections.abc import Sequence
from dataclasses import replace

import pytest
from exar.pydantic_ai import definition_action
from pydantic_ai import FunctionToolset
from pydantic_ai.agent import AgentInstructions
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents import capabilities
from hivegent.agents.app import chat_agent
from hivegent.agents.capabilities import (
    FEATURES,
    build_capabilities,
    collect_tool_schemas,
)
from hivegent.agents.common import UserDeps
from hivegent.agents.guards import SPILL_TOOLS
from hivegent.agents.tools.python import sandbox_surface
from hivegent.prompts import GROUNDING_INSTRUCTIONS
from hivegent.server.routes.meta import list_tools
from hivegent.store import User, UserScope
from hivegent.types import DocumentFilter, Mode
from tests.helpers import in_run, owned, run_context

_EXPLORE = next(feature for feature in FEATURES if feature.id == "explore")


def _static_texts(instructions: AgentInstructions[UserDeps]) -> list[str]:
    """The literal blocks in *instructions*, skipping the callables."""
    if isinstance(instructions, str):
        return [instructions]

    if isinstance(instructions, Sequence):
        return [part for part in instructions if isinstance(part, str)]

    return []


def _deps(disabled: Sequence[str] = (), mode: Mode = "interactive") -> UserDeps:
    return UserDeps(user=User("u"), mode=mode, disabled_tools=frozenset(disabled))


def _instructions(disabled: Sequence[str]) -> str:
    """Every static instruction the composed capabilities contribute."""
    return "\n".join(
        text
        for capability in build_capabilities(_deps(disabled))
        for text in _static_texts(capability.get_instructions())
    )


def test_grounding_rides_with_the_retrieval_tools() -> None:
    """Disabling every explore tool must retract the search-first mandate."""
    assert GROUNDING_INSTRUCTIONS["en"] in _instructions(())

    disabled = sorted(_EXPLORE.tool_names)
    assert GROUNDING_INSTRUCTIONS["en"] not in _instructions(disabled)


def test_shared_block_survives_on_its_remaining_feature() -> None:
    """Path guidance is owned by explore *and* write, so write alone keeps it."""
    disabled = sorted(_EXPLORE.tool_names)
    ids = {capability.id for capability in build_capabilities(_deps(disabled))}
    assert "workspace-paths" in ids


def _turn(
    *,
    relevant: frozenset[str] = frozenset(),
    mode: Mode = "interactive",
    disabled: Sequence[str] = (),
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
        disabled_tools=frozenset(disabled),
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


def test_document_scope_rides_with_the_retrieval_tools() -> None:
    """The note naming the user's selection must reach the model.

    It is the only thing that ties a bare "these documents" to the files the
    eye toggle picked, and being computed per request it is invisible to the
    static checks above, so it is asserted on its own.
    """
    selected = frozenset({"~/a.md"})
    assert "~/a.md" in _instruction_text(_turn(relevant=selected))

    disabled = sorted(_EXPLORE.tool_names)
    assert "~/a.md" not in _instruction_text(
        _turn(relevant=selected, disabled=disabled)
    )


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


def test_an_operator_exclusion_retracts_the_feature_it_empties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One namespace, one mechanism: it drops instructions as a user's does."""
    monkeypatch.setattr(
        capabilities.settings.tools, "disabled", sorted(_EXPLORE.tool_names)
    )

    assert GROUNDING_INSTRUCTIONS["en"] not in _instructions(())


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
                    capabilities=build_capabilities(_deps(), extra=[extra]),
                ),
            )
        )
        return {tool.name for tool in captured[0].function_tools}

    assert "remote_thing" in names()

    monkeypatch.setattr(capabilities.settings.tools, "disabled", ["remote_thing"])
    assert "remote_thing" not in names()


async def test_an_excluded_tool_offers_the_user_no_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settings dialog renders a checkbox per entry of this listing.

    A tool the deployment withholds must not appear there switched on, so the
    user-facing listing drops the exclusions while the admin console, which is
    where the name to exclude is read, keeps them.
    """
    monkeypatch.setattr(capabilities.settings.tools, "disabled", ["query_table"])

    assert "query_table" in {tool.name for tool in collect_tool_schemas()}
    assert "query_table" not in {tool.name for tool in await list_tools(User(id="u"))}


async def test_an_internal_tool_is_listed_but_never_switchable() -> None:
    """The host reports the assistant through it, so no list may withhold it."""
    listed = {tool.name: tool for tool in await list_tools(User(id="u"))}
    assistant = next(feature for feature in FEATURES if feature.id == "assistant")
    (tool_def,) = assistant.tool_defs

    assert not listed["read_assistant"].switchable
    assert capabilities.unknown_tool_names(["read_assistant"]) == ["read_assistant"]
    assert _deps(["read_assistant"]).policy(definition_action(tool_def)) == "allow"


def test_a_misspelled_exclusion_is_a_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It would withhold nothing, and the only symptom is a schema still there."""
    capabilities.check_tool_settings()

    monkeypatch.setattr(capabilities.settings.tools, "disabled", ["query_tables"])
    with pytest.raises(ValueError, match="query_tables"):
        capabilities.check_tool_settings()


def test_delegation_is_known_and_listed() -> None:
    """A list may withhold the delegation tools and the subagents, and the catalog names them."""
    infos = {info.name: info for info in capabilities.tool_infos()}

    assert capabilities.unknown_tool_names(["delegate_task", "explore"]) == []
    assert {"delegate_task", "cancel_task", "explore", "load_skill"} <= infos.keys()
    assert infos["delegate_task"].switchable
    assert not any(infos[name].switchable for name in SPILL_TOOLS)


async def test_a_withheld_tool_is_called_through_no_surface() -> None:
    """The debug console and MCP build the tools by the same rule a run does."""
    with pytest.raises(KeyError):
        await capabilities.invoke_agent_tool(
            "delete_documents", {"file_paths": ["~/a.md"]}, _deps(["delete_documents"])
        )


def test_an_allowlist_withholds_derived_content() -> None:
    """Past conversations and the memory may quote any document beyond the allowlist."""
    memory = next(feature for feature in FEATURES if feature.id == "memory")
    confined = replace(
        _deps(), document_filters={UserScope("u"): DocumentFilter(allowed=frozenset())}
    )

    assert memory.live(_deps())
    assert not memory.live(confined)
