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


def test_agent_tool_takes_its_description_and_arguments_from_the_function() -> None:
    tool = _tool(explore_toolset, "list_documents")
    schema = tool.function_schema.json_schema

    assert tool.description is not None
    assert tool.description.startswith("List available documents with sizes and dates.")
    assert schema["properties"]["path"]["description"] == _description(LsPathArg)
    assert schema["properties"]["max_depth"]["description"] == _description(
        LsMaxDepthArg
    )


def test_agent_search_tool_uses_consistent_search_type_name() -> None:
    schema = _tool(explore_toolset, "search").function_schema.json_schema

    assert "search_type" in schema["properties"]
    assert "type" not in schema["properties"]
    assert schema["properties"]["search_type"]["description"] == _description(
        SearchTypeArg
    )


def test_agent_table_tool_exposes_configurable_limit() -> None:
    schema = _tool(explore_toolset, "query_table").function_schema.json_schema

    assert schema["properties"]["limit"]["description"] == _description(TableLimitArg)


def test_agent_python_tool_describes_monty_constraints() -> None:
    tool = _tool(python_toolset, "run_python")
    schema = tool.function_schema.json_schema
    description = tool.description

    assert description is not None
    assert "Monty interpreter" in description
    assert "subset of Python and its standard library" in description
    assert schema["properties"]["code"]["description"] == _description(CodeArg)
    assert schema["properties"]["script_path"]["description"] == _description(
        PythonScriptPathArg
    )
    assert set(schema["properties"]) == {"code", "script_path", "args"}


async def test_mcp_tool_reuses_the_agent_tool_description_and_arguments() -> None:
    tool = await _mcp_tool("grep")

    assert tool.description == _tool(explore_toolset, "grep").description
    assert tool.parameters["properties"]["pattern"]["description"] == _description(
        GrepPatternArg
    )
    assert tool.parameters["properties"]["context"]["description"] == _description(
        GrepContextArg
    )


async def test_mcp_exposes_jq_named_by_the_document_reader() -> None:
    assert (await _mcp_tool("jq")).name == "jq"


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
