"""Capability composition: guidance appears exactly when its tools do."""

from collections.abc import Sequence

from pydantic_ai.agent import AgentInstructions

from hivegent.agents.capabilities import FEATURES, build_capabilities
from hivegent.agents.common import UserDeps
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


def _instructions(spec: ToolsSpec) -> str:
    """Every static instruction the composed capabilities contribute."""
    return "\n".join(
        text
        for capability in build_capabilities(spec)
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
    ids = {capability.id for capability in build_capabilities(disabled)}
    assert "workspace-paths" in ids
