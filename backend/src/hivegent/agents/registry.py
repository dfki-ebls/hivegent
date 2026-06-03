"""Toolset grouping and metadata helpers for agents."""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic_ai import FilteredToolset, FunctionToolset, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage

from ..tools.pydantic_ai import unwrap_tool_output
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
    "TOOLSET_GROUPS",
    "build_toolsets",
    "collect_tool_schemas",
    "invoke_agent_tool",
]

TOOLSET_GROUPS: dict[str, FunctionToolset[UserDeps]] = {
    "explore": explore_toolset,
    "subagent": subagent_toolset,
    "write": write_toolset,
    "memory": memory_toolset,
    "web": web_toolset,
    "conversation": conversation_toolset,
    "plan": plan_toolset,
}

PLAN_MODE_GROUPS: frozenset[str] = frozenset(
    {
        "explore",
        "subagent",
        "web",
        "conversation",
        "plan",
    }
)

EXECUTE_MODE_GROUPS: frozenset[str] = frozenset(
    {
        "explore",
        "subagent",
        "write",
        "memory",
        "web",
        "conversation",
    }
)


def build_toolsets(
    toolset_groups: dict[str, FunctionToolset[UserDeps]],
    tools_spec: ToolsSpec,
    extra: Sequence[AbstractToolset[UserDeps]] = (),
    mode: Literal["plan", "execute"] = "execute",
) -> Sequence[AbstractToolset[UserDeps]]:
    """Filter toolsets by mode, apply disabled-tool filtering, and append extras.

    Args:
        toolset_groups: Mapping of group name to toolset.
        tools_spec: Combined tool configuration from the chat request.
        extra: Additional toolsets to append (e.g. MCP servers).
        mode: Agent mode controlling which toolset groups are included.

    Returns:
        Sequence of toolsets ready to pass to the agent.
    """
    allowed = PLAN_MODE_GROUPS if mode == "plan" else EXECUTE_MODE_GROUPS
    toolsets = [ts for name, ts in toolset_groups.items() if name in allowed]

    result: list[AbstractToolset[UserDeps]] = []

    if tools_spec.disabled_tools:
        disabled = frozenset(tools_spec.disabled_tools)
        result.extend(
            FilteredToolset(
                wrapped=toolset,
                filter_func=lambda _ctx, td, _disabled=disabled: (
                    td.name not in _disabled
                ),
            )
            for toolset in toolsets
        )
    else:
        result.extend(toolsets)

    result.extend(extra)
    return result


def collect_tool_schemas[T](
    toolset_groups: dict[str, FunctionToolset[T]],
) -> list[ToolSchema]:
    """Collect each tool's metadata and the JSON Schema of its parameters.

    The richer counterpart to a bare name/description listing: callers that
    only need :class:`ToolInfo` fields rely on the response model to drop
    ``parameters``.

    Args:
        toolset_groups: Mapping of group name to toolset.

    Returns:
        Flat list of tool schema entries.
    """
    return [
        ToolSchema(
            name=name,
            description=tool.description or "",
            group=group,
            parameters=tool.function_schema.json_schema,
        )
        for group, toolset in toolset_groups.items()
        for name, tool in toolset.tools.items()
    ]


async def invoke_agent_tool[D](
    toolset_groups: dict[str, FunctionToolset[D]],
    tool_name: str,
    args: dict[str, Any],
    deps: D,
) -> tuple[str | None, Any]:
    """Validate ``args``, run ``tool_name`` with ``deps``, and unwrap the result.

    Runs the exact code path the agent uses, so stateful behaviour (e.g.
    pgvector retrieval) is exercised identically.  The placeholder model on
    the run context is never invoked: every tool that talks to an LLM builds
    its own model from ``deps`` or settings.

    Args:
        toolset_groups: Mapping of group name to toolset.
        tool_name: Name of the tool to invoke.
        args: Raw argument mapping, validated against the tool's schema.
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).

    Returns:
        A ``(text, structured_data)`` pair.

    Raises:
        KeyError: If no tool named ``tool_name`` is registered.
        pydantic.ValidationError: If ``args`` fail the tool's schema.
    """
    for toolset in toolset_groups.values():
        if tool_name in toolset.tools:
            tool = toolset.tools[tool_name]
            break
    else:
        raise KeyError(tool_name)

    schema = tool.function_schema
    validated = schema.validator.validate_python(args)
    ctx = RunContext(
        deps=deps, model=TestModel(), usage=RunUsage(), tool_name=tool_name
    )
    return unwrap_tool_output(await schema.call(validated, ctx))
