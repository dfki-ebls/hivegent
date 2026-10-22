"""Serve the agent's own tools over MCP, run the way the agent runs them.

Each call builds the caller's :class:`UserDeps` in ``interactive`` mode with no
conversation, so there is no ``/tmp`` and a workspace write asks the user first.
That question is the write gate's :class:`ApprovalRequired`, answered with an MCP
elicitation, and the confirmed retry runs the call approved as an agent's
resumed one does.

Which tools are served is the server's to settle, not the caller's: each tool's
``prepare`` hook is asked once at import, with a caller's deps but no caller,
and a tool it withholds, such as ``apply_changes`` without a conversation, is
never served.
"""

import inspect
from collections.abc import Mapping
from typing import Any, override

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token, get_context
from fastmcp.tools import Tool as McpTool
from fastmcp.tools import ToolResult
from fastmcp.tools.base import InputRequiredToolResult
from mcp.types import ContentBlock, TextContent, ToolAnnotations
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import BinaryContent, Tool
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_json

from ..agents import UserDeps, agent_tool, invoke_agent_tool, unlisted_tool_names
from ..agents.capabilities import feature_tools
from ..auth import dev_user, user_from_claims
from ..changes import ChangesetSummary
from ..config import content_hash, settings
from ..store import User
from ..tools.base import BinaryAttachment
from ..tools.fastmcp import attachment_block
from ..tools.formatting import truncate_middle
from ..tools.pydantic_ai import run_context, unwrap_tool_output
from ..types import ToolsSpec
from .confirmation import confirm

__all__ = ["get_mcp_user", "serve_agent_tools"]

_FEATURES = frozenset({"explore", "write", "subagent"})
"""The features served, the ones that need neither a conversation nor a sandbox."""

_SUMMARY = TypeAdapter(ChangesetSummary)


async def get_mcp_user(access_token: AccessToken | None) -> User:
    """Build the caller's :class:`User` from the MCP auth token.

    The same construction the HTTP API uses, so an MCP client is granted
    exactly the groups and permissions its token grants there.
    """
    if not settings.auth.enable:
        return await dev_user()

    if access_token is None:
        raise RuntimeError("No authenticated user in MCP context")

    return user_from_claims(access_token.claims)


def _deps(user: User) -> UserDeps:
    return UserDeps(user=user, mode="interactive")


def _question(summary: ChangesetSummary) -> str:
    diffs = (f"{c.path}\n{c.diff}" for c in (*summary.creates, *summary.updates))

    return "\n\n".join(("Allow these changes?", "\n".join(summary.lines()), *diffs))


def _block(content: BinaryContent) -> ContentBlock:
    return attachment_block(
        BinaryAttachment(content.data, content.media_type, content.identifier)
    )


def _result(value: object) -> ToolResult:
    """The MCP rendering of a return: its text within the MCP output bound, and its attachments.

    The structured data is left out.  It is our frontend's, and clients that
    get it hand it to their model in place of the text, or beside it.
    """
    output = unwrap_tool_output(value)
    text = output.text if output.text is not None else to_json(output.data).decode()
    limit = settings.mcp.output_max_chars
    bounded = text if limit is None else truncate_middle(text, limit)
    blocks: list[ContentBlock] = [TextContent(type="text", text=bounded)]
    blocks.extend(
        _block(item) for item in output.rest or () if isinstance(item, BinaryContent)
    )

    return ToolResult(content=blocks)


async def _call(name: str, arguments: Mapping[str, Any], deps: UserDeps) -> ToolResult:
    """Call the tool, asking the user first wherever the write gate asks."""
    tool = agent_tool(name)

    try:
        return _result(await invoke_agent_tool(tool, arguments, deps))
    except ApprovalRequired as exc:
        question = _question(_SUMMARY.validate_python(exc.metadata))
        digest = content_hash(to_json((name, arguments, exc.metadata)).decode())

        if (ask := confirm(get_context(), question, digest)) is not None:
            return InputRequiredToolResult(ask)

    return _result(await invoke_agent_tool(tool, arguments, deps, approved=True))


class AgentTool(McpTool):
    """One agent tool, validated, gated, and called as the agent calls it."""

    @override
    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        deps = _deps(await get_mcp_user(get_access_token()))

        try:
            return await _call(self.name, arguments, deps)
        except ModelRetry as exc:
            raise ToolError(exc.message) from exc
        except ValidationError as exc:
            raise ToolError(str(exc)) from exc


def _prepared(tool: Tool[UserDeps], deps: UserDeps) -> ToolDefinition | None:
    """*tool*'s definition after its ``prepare`` hook, which must answer at once to be served."""
    if tool.prepare is None:
        return tool.tool_def

    result = tool.prepare(run_context(deps), tool.tool_def)

    if inspect.isawaitable(result):
        raise TypeError(
            f"{tool.name}'s prepare hook must be synchronous to be served over MCP"
        )

    return result


def serve_agent_tools(app: FastMCP) -> None:
    """Serve every tool of :data:`_FEATURES` the operator and its ``prepare`` hook leave."""
    unlisted = unlisted_tool_names(ToolsSpec())
    # No hook may depend on who calls, so the probe names no one.
    probe = _deps(User(""))

    for feature_id, tool in feature_tools():
        if feature_id not in _FEATURES or tool.name in unlisted:
            continue

        if (tool_def := _prepared(tool, probe)) is None:
            continue

        writes = feature_id == "write"
        app.add_tool(
            AgentTool(
                name=tool.name,
                description=tool_def.description,
                parameters=tool_def.parameters_json_schema,
                annotations=ToolAnnotations(
                    read_only_hint=not writes, destructive_hint=writes
                ),
            )
        )
