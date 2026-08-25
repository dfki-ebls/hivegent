"""Capability composition: guidance appears exactly when its tools do."""

from collections.abc import Sequence

from pydantic_ai.agent import AgentInstructions

from hivegent.agents.capabilities import FEATURES, build_capabilities
from hivegent.agents.common import UserDeps, scope_instructions
from hivegent.prompts import GROUNDING_INSTRUCTIONS
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
