"""Toolset grouping and metadata helpers for agents."""

from collections.abc import Sequence

from pydantic_ai import FilteredToolset, FunctionToolset
from pydantic_ai.toolsets import AbstractToolset

from ..types import ToolInfo, ToolsSpec
from .common import UserDeps
from .tools import (
    conversation_toolset,
    explore_toolset,
    memory_toolset,
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
}


def build_toolsets[T](
    toolsets: Sequence[FunctionToolset[T]],
    tools_spec: ToolsSpec,
    extra: Sequence[AbstractToolset[T]] = (),
) -> Sequence[AbstractToolset[T]]:
    """Apply disabled-tool filtering and append extra toolsets.

    Args:
        toolsets: Built-in agent toolsets.
        tools_spec: Combined tool configuration from the chat request.
        extra: Additional toolsets to append (e.g. MCP servers).

    Returns:
        Sequence of toolsets ready to pass to the agent.
    """
    result: list[AbstractToolset[T]] = []

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
