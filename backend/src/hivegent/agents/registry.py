"""Toolset grouping and metadata helpers for agents."""

from collections.abc import Sequence
from typing import Literal

from pydantic_ai import FilteredToolset, FunctionToolset
from pydantic_ai.toolsets import AbstractToolset

from ..types import ToolInfo, ToolsSpec
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

__all__ = ["TOOLSET_GROUPS", "build_toolsets", "collect_tool_info"]

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


def collect_tool_info[T](
    toolset_groups: dict[str, FunctionToolset[T]],
) -> list[ToolInfo]:
    """Collect metadata from all registered toolset groups.

    Args:
        toolset_groups: Mapping of group name to toolset.

    Returns:
        Flat list of tool info entries.
    """
    result: list[ToolInfo] = []
    for group, toolset in toolset_groups.items():
        for name, tool in toolset.tools.items():
            result.append(
                ToolInfo(name=name, description=tool.description or "", group=group)
            )
    return result
