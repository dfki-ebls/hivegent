"""Capability composition and the agent feature registry.

A capability is the pydantic-ai v2 primitive that bundles a feature's toolset
with the instructions, hooks, and model settings that belong to it, so a whole
feature reaches the agent through one composable unit.

This module is the single source of truth for the agent's features:
:data:`FEATURES` lists each one as a :class:`Feature` (a capability plus the
modes it is offered in), and every consumer derives from it. The agent run
composes from the capabilities, while the debug/meta REST surface lists and
invokes individual tools extracted from the very same capabilities, so an admin
inspects exactly the tools an agent is built from.

Cross-cutting agent behaviour (personality, language, citation, math, image)
stays as agent-level ``instructions`` because it is not bound to any one feature.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.agent import AgentInstructions
from pydantic_ai.capabilities import AbstractCapability, Capability, PrepareTools
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.tools import ToolDefinition

from ..db.memory import load_memory
from ..prompts import (
    MEMORY_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS_EMPTY,
    PLAN_INSTRUCTIONS,
)
from ..tools.pydantic_ai import capability_tools, invoke_tool
from ..types import ToolSchema, ToolsSpec
from .common import UserDeps
from .tools import (
    conversation_toolset,
    explore_toolset,
    memory_toolset,
    plan_toolset,
    subagent_toolset,
    web_toolset,
    write_toolset,
)

__all__ = [
    "FEATURES",
    "Feature",
    "build_capabilities",
    "collect_tool_schemas",
    "invoke_agent_tool",
]


type Mode = Literal["plan", "execute"]

_ALL_MODES: frozenset[Mode] = frozenset({"plan", "execute"})
_EXECUTE: frozenset[Mode] = frozenset({"execute"})
_PLAN: frozenset[Mode] = frozenset({"plan"})


async def _memory_instructions(ctx: RunContext[UserDeps]) -> str:
    """Inject the user's persisted memory alongside the save-memory guidance."""
    content = await load_memory(ctx.deps.user_id)

    if content:
        return MEMORY_INSTRUCTIONS.format(memory_content=content)

    return MEMORY_INSTRUCTIONS_EMPTY


@dataclass(frozen=True, slots=True)
class Feature:
    """A named agent feature: one capability plus the modes it is offered in.

    The ``capability`` bundles the feature's toolset, instructions, and (later)
    hooks; ``modes`` is the only selection metadata pydantic-ai has no concept
    of.  Its ``id`` is the capability's ``id``, named once via :func:`_feature`.
    ``tool_names`` is its set of tool names, resolved once so the per-request
    disabled-tool check needs no toolset walk.
    """

    id: str
    capability: Capability[UserDeps]
    modes: frozenset[Mode] = _ALL_MODES
    tool_names: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "tool_names", frozenset(capability_tools(self.capability))
        )


def _feature(
    id: str,
    toolset: FunctionToolset[UserDeps],
    *,
    instructions: AgentInstructions[UserDeps] | None = None,
    modes: frozenset[Mode] = _ALL_MODES,
) -> Feature:
    """Name a feature once, bundling its toolset and instructions into a capability."""
    return Feature(
        id, Capability(id=id, toolsets=[toolset], instructions=instructions), modes
    )


# The single source of truth for the agent's features.  ``plan`` and ``memory``
# carry their own instructions (``memory`` resolves the user's stored memory
# lazily, so its guidance only loads when active); the rest are bare toolset
# bundles.  Adding a feature here exposes it to the agent and the debug surface.
FEATURES: tuple[Feature, ...] = (
    _feature("explore", explore_toolset),
    _feature("subagent", subagent_toolset),
    _feature("write", write_toolset, modes=_EXECUTE),
    _feature(
        "memory",
        memory_toolset,
        instructions=_memory_instructions,
        modes=_EXECUTE,
    ),
    _feature("web", web_toolset),
    _feature("conversation", conversation_toolset),
    _feature("plan", plan_toolset, instructions=PLAN_INSTRUCTIONS, modes=_PLAN),
)


def _filter_disabled(disabled: frozenset[str]) -> AbstractCapability[UserDeps]:
    """A capability that hides every disabled tool from the model, group-agnostic."""

    def prepare(
        _ctx: RunContext[UserDeps], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        return [td for td in tool_defs if td.name not in disabled]

    return PrepareTools(prepare, id="disabled-tools")


def build_capabilities(
    tools_spec: ToolsSpec,
    *,
    extra: Sequence[AbstractToolset[UserDeps]] = (),
    mode: Mode = "execute",
) -> Sequence[AbstractCapability[UserDeps]]:
    """Compose the capabilities for an agent run.

    Selects the features offered in ``mode``, drops any whose tools are all
    disabled (so a fully-disabled feature contributes neither tools nor
    instructions), hides individually disabled tools via a single
    :class:`PrepareTools` capability, and wraps each extra toolset (e.g. an MCP
    server) as its own capability.

    Args:
        tools_spec: Combined tool configuration from the chat request.
        extra: Additional toolsets to expose (e.g. MCP servers).
        mode: Agent mode controlling which features are included.

    Returns:
        Sequence of capabilities ready to pass to the agent.
    """
    disabled = frozenset(tools_spec.disabled_tools or ())

    result: list[AbstractCapability[UserDeps]] = [
        feature.capability
        for feature in FEATURES
        if mode in feature.modes and not feature.tool_names <= disabled
    ]

    if disabled:
        result.append(_filter_disabled(disabled))

    result.extend(Capability(toolsets=[toolset]) for toolset in extra)
    return result


def collect_tool_schemas() -> list[ToolSchema]:
    """Collect each tool's metadata and the JSON Schema of its parameters.

    Derived from the same :data:`FEATURES` the agent is composed from, grouped
    by feature id, so the listing matches what an agent can actually call.  The
    richer counterpart to a bare name/description listing: callers that only
    need :class:`ToolInfo` fields rely on the response model to drop
    ``parameters``.

    Returns:
        Flat list of tool schema entries.
    """
    return [
        ToolSchema(
            name=name,
            description=tool.description or "",
            group=feature.id,
            parameters=tool.function_schema.json_schema,
        )
        for feature in FEATURES
        for name, tool in capability_tools(feature.capability).items()
    ]


async def invoke_agent_tool(
    tool_name: str, args: dict[str, Any], deps: UserDeps
) -> tuple[str | None, Any]:
    """Validate ``args``, run ``tool_name`` with ``deps``, and unwrap the result.

    Looks the tool up across every feature capability and runs it through the
    exact code path the agent uses (see :func:`hivegent.tools.pydantic_ai.invoke_tool`).

    Args:
        tool_name: Name of the tool to invoke.
        args: Raw argument mapping, validated against the tool's schema.
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).

    Returns:
        A ``(text, structured_data)`` pair.

    Raises:
        KeyError: If no tool named ``tool_name`` is registered.
        pydantic.ValidationError: If ``args`` fail the tool's schema.
    """
    for feature in FEATURES:
        if tool_name in feature.tool_names:
            tool = capability_tools(feature.capability)[tool_name]
            return await invoke_tool(tool, args, deps)

    raise KeyError(tool_name)
