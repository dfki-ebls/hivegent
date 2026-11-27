"""Serve the agent's own tools over MCP, run the way the agent runs them.

`exar.mcp.server` serves the tools `agent_tools` builds for each request,
bound to the caller's :class:`UserDeps` in ``interactive`` mode with no
conversation, so there is no ``/tmp`` and a workspace write asks the user first.
That question is the write gate's :class:`ApprovalRequired`, asked over exar's
SEP-2322 round trip (`exar.mcp.Confirmations`) with the changeset it would make,
and the confirmed retry runs the call approved and bound to the digest of that
changeset, so the gate refuses it once the changes are different ones.
Plugin tools are served alike, so one whose effect is not ``read`` asks before
every call, as it does in a chat.

Which tools are served is the server's to settle and the caller's deps to
narrow: each tool's ``prepare`` hook is asked once, with a caller's deps but no
caller, and a tool it withholds, such as ``apply_changes`` without a
conversation, is never served, while each request lists and calls only what
the caller's policy lists (``agents.policy.listed``).
"""

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from typing import Annotated, Any, override

from exar.mcp import (
    Confirmations,
    ToolFactory,
    annotations,
    describe_call,
    tool_content,
)
from exar.pydantic_ai import definition_action
from exar.text import truncate_middle
from exar.tools.batch import unpack
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.tools import Tool as McpTool
from fastmcp.tools import ToolResult
from fastmcp.tools.base import InputRequiredToolResult
from mcp.types import ToolAnnotations
from pydantic import Field, InstanceOf, TypeAdapter, ValidationError
from pydantic.json_schema import SkipJsonSchema
from pydantic_ai import Tool
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.tools import ToolDefinition

from ..agents import UserDeps, invoke_agent_tool
from ..agents.approval import APPROVED_CHANGES, changes_digest
from ..agents.capabilities import FEATURES, PLUGIN_FEATURE_IDS, Feature
from ..agents.common import run_context
from ..agents.policy import host_policy, listed
from ..auth import PROBE, user_from_claims
from ..config import settings
from ..store import User
from ..tools.changes import ChangesetSummary

__all__ = ["POLICY", "SERVED_FEATURES", "Caller", "agent_tools", "token_user"]

_FEATURES = frozenset({"explore", "write"})
"""The built-in features served, the ones that need neither a conversation nor a sandbox.

Every plugin feature is served beside them, since a plugin tool needs neither.
"""

SERVED_FEATURES: tuple[Feature, ...] = tuple(
    feature
    for feature in FEATURES
    if feature.id in _FEATURES or feature.id in PLUGIN_FEATURE_IDS
)
"""The features whose tools are served, the built-in ones of :data:`_FEATURES` and the plugins'."""

_MODE = "interactive"
"""The mode of every served call, in which a workspace write asks the user first."""

POLICY = host_policy(_MODE)
"""The policy of the served tools, which each call applies as an agent's does (`invoke_agent_tool`)."""

_SUMMARY = TypeAdapter(ChangesetSummary | None)

type Caller = Callable[[AccessToken | None], Awaitable[User]]
"""Who calls the served tools, resolved anew for every request from its access token."""


async def token_user(token: AccessToken | None) -> User:
    """Build the caller's :class:`User` from the MCP access token.

    The same construction the HTTP API uses, so an MCP client is granted
    exactly the groups and permissions its token grants there.
    """
    if token is None:
        raise RuntimeError("No authenticated user in MCP context")

    user = user_from_claims(token.claims)
    # A service answers many people at once and reaches the chat route alone.
    user.require_person()

    return user


def _deps(user: User) -> UserDeps:
    return UserDeps(user=user, mode=_MODE)


async def _question(
    name: str, arguments: Mapping[str, object], summary: ChangesetSummary | None
) -> str:
    if summary is None:
        return await describe_call(name, arguments)

    diffs = (f"{c.path}\n{c.diff}" for c in (*summary.creates, *summary.updates))

    return "\n\n".join(("Allow these changes?", "\n".join(summary.lines()), *diffs))


def _bounded(text: str) -> str:
    """*text* within the MCP output bound, which a client cannot read on past."""
    limit = settings.mcp.output_max_chars

    return text if limit is None else truncate_middle(text, limit)


def _result(value: object) -> ToolResult:
    """The MCP rendering of a return: its text within the MCP output bound, and its attachments.

    The structured data is left out.  It is our frontend's, and clients that
    get it hand it to their model in place of the text, or beside it.
    """
    unpacked = unpack(value)

    return ToolResult(
        content=tool_content(replace(unpacked, text=_bounded(unpacked.text)))
    )


async def _call(
    name: str, arguments: Mapping[str, Any], deps: UserDeps, confirm: Confirmations
) -> ToolResult:
    """Call the tool, asking the user first wherever the write gate asks."""
    try:
        return _result(await invoke_agent_tool(name, arguments, deps))
    except ApprovalRequired as exc:
        summary = _SUMMARY.validate_python(exc.metadata)

        if (
            ask := await confirm(
                name, arguments, await _question(name, arguments, summary)
            )
        ) is not None:
            return InputRequiredToolResult(ask)

    approval = {APPROVED_CHANGES: changes_digest(summary)}

    return _result(
        await invoke_agent_tool(name, arguments, deps, approved=True, metadata=approval)
    )


class AgentTool(McpTool):
    """One agent tool for one caller, validated, gated, and called as the agent calls it."""

    deps: Annotated[SkipJsonSchema[InstanceOf[UserDeps]], Field(exclude=True)]
    confirm: Annotated[SkipJsonSchema[InstanceOf[Confirmations]], Field(exclude=True)]

    @override
    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            return await _call(self.name, arguments, self.deps, self.confirm)
        except ModelRetry as exc:
            raise ToolError(_bounded(exc.message)) from exc
        except ValidationError as exc:
            raise ToolError(_bounded(str(exc))) from exc


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


def _served(
    features: Iterable[Feature],
) -> list[tuple[ToolDefinition, ToolAnnotations]]:
    """Every tool of *features* that its ``prepare`` hook and :data:`POLICY` leave."""
    # No hook may depend on who calls, so the probe names no one.
    probe = _deps(PROBE)

    return [
        (tool_def, annotations(action.effect))
        for feature in features
        for tool in feature.tools.build(run_context(probe)).tools.values()
        if (tool_def := _prepared(tool, probe)) is not None
        and POLICY(action := definition_action(tool_def)) != "deny"
    ]


def agent_tools(
    caller: Caller,
    confirm: Confirmations,
    features: Iterable[Feature] = SERVED_FEATURES,
) -> ToolFactory:
    """The tools of *features* served to each request, every one called as the *caller* its access token names.

    A write the gate asks about asks the user through *confirm*.
    """
    served = _served(features)

    async def tools(token: AccessToken | None) -> list[AgentTool]:
        # Resolved once per request off the event loop, as a turn's are, rather than by every read tool.
        deps = await _deps(await caller(token)).with_roots()

        return [
            AgentTool(
                name=tool_def.name,
                description=tool_def.description,
                parameters=tool_def.parameters_json_schema,
                annotations=hints,
                deps=deps,
                confirm=confirm,
            )
            for tool_def, hints in served
            if listed(deps.policy, definition_action(tool_def))
        ]

    return tools
