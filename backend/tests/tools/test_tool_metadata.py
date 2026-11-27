import pytest
from exar.mcp import Confirmations, server
from exar.tools.files import GrepContextArg, GrepPatternArg, LsMaxDepthArg, LsPathArg
from exar.tools.table import TableLimitArg
from fastmcp.server.auth import AccessToken
from fastmcp.tools import Tool as McpTool
from pydantic import TypeAdapter
from pydantic_ai import Tool
from typing_extensions import TypeForm

from hivegent.agents.capabilities import FEATURES
from hivegent.agents.common import SELF_BOUNDED, AgentTools, UserDeps, probe_context
from hivegent.agents.tools import explore_toolset, python_toolset
from hivegent.auth import PROBE
from hivegent.config import settings
from hivegent.mcp.adapter import POLICY, agent_tools
from hivegent.store import User
from hivegent.tools.python import CodeArg, PythonScriptPathArg
from hivegent.tools.retrieval import SearchTypeArg


def _description(annotation: TypeForm[object]) -> str | None:
    return TypeAdapter(annotation).json_schema().get("description")


def _tool(tools: AgentTools, name: str) -> Tool[UserDeps]:
    return tools.build(probe_context()).tools[name]


async def _mcp_tool(name: str) -> McpTool:
    """The tool *name* as MCP serves it."""

    async def caller(_token: AccessToken | None) -> User:
        return PROBE

    confirmations = Confirmations(key=b"test")
    tool = await server(
        agent_tools(caller, confirmations),
        name="probe",
        policy=POLICY,
        confirmations=confirmations,
    ).get_tool(name)

    assert tool is not None

    return tool


@pytest.mark.parametrize(
    ("tools", "name", "arguments"),
    [
        (
            explore_toolset,
            "list_documents",
            {"path": LsPathArg, "max_depth": LsMaxDepthArg},
        ),
        (explore_toolset, "search", {"search_type": SearchTypeArg}),
        (explore_toolset, "query_table", {"limit": TableLimitArg}),
        (
            python_toolset,
            "run_python",
            {"code": CodeArg, "script_path": PythonScriptPathArg},
        ),
    ],
)
def test_agent_tool_takes_its_arguments_from_the_function(
    tools: AgentTools, name: str, arguments: dict[str, TypeForm[object]]
) -> None:
    schema = _tool(tools, name).function_schema.json_schema["properties"]

    for argument, annotation in arguments.items():
        assert schema[argument]["description"] == _description(annotation)

    assert "type" not in schema


def test_agent_python_tool_describes_monty_constraints() -> None:
    tool = _tool(python_toolset, "run_python")

    assert tool.description is not None
    assert "Monty interpreter" in tool.description
    assert "subset of Python and its standard library" in tool.description
    assert set(tool.function_schema.json_schema["properties"]) == {
        "code",
        "script_path",
        "args",
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("grep", {"pattern": GrepPatternArg, "context": GrepContextArg}), ("jq", {})],
)
async def test_mcp_tool_reuses_the_agent_tool_description_and_arguments(
    name: str, arguments: dict[str, TypeForm[object]]
) -> None:
    tool = await _mcp_tool(name)

    assert tool.name == name
    assert tool.description == _tool(explore_toolset, name).description

    for argument, annotation in arguments.items():
        assert tool.parameters["properties"][argument]["description"] == _description(
            annotation
        )


def test_every_tool_call_is_bounded() -> None:
    """Every feature carries the default, and only a tool that bounds itself opts out."""
    timeouts = {
        name: feature.tools.timeout
        for feature in FEATURES
        for name in feature.tool_names
    }

    assert {
        name
        for name, timeout in timeouts.items()
        if timeout != settings.llm.tool_timeout_seconds
    } == {"run_python"}
    assert timeouts["run_python"] == SELF_BOUNDED
