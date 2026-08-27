from pydantic import TypeAdapter

from hivegent.agents import explore_toolset
from hivegent.agents.tools import compute_toolset
from hivegent.mcp import mcp_app
from hivegent.tools.base import tool_description
from hivegent.tools.documents import (
    DocumentMaxDepthArg,
    DocumentPathArg,
    ListDocumentsTool,
)
from hivegent.tools.grep import GrepContextArg, GrepPatternArg, GrepTool
from hivegent.tools.python import (
    CodeArg,
    PythonInputPathsArg,
    PythonOutputPathArg,
    PythonScriptPathArg,
    RunPythonTool,
)
from hivegent.tools.retrieval import SearchTypeArg
from hivegent.tools.table import QueryTableTool, TableRowLimitArg


def _description(annotation: object) -> str | None:
    return TypeAdapter(annotation).json_schema().get("description")


def test_agent_tool_reuses_canonical_docstring_and_alias_metadata() -> None:
    tool = explore_toolset.tools["list_documents"]
    schema = tool.function_schema.json_schema

    assert tool.description == tool_description(ListDocumentsTool)
    assert schema["properties"]["path"]["description"] == _description(DocumentPathArg)
    assert schema["properties"]["max_depth"]["description"] == _description(
        DocumentMaxDepthArg
    )


def test_agent_search_tool_uses_consistent_search_type_name() -> None:
    tool = explore_toolset.tools["search"]
    schema = tool.function_schema.json_schema

    assert "search_type" in schema["properties"]
    assert "type" not in schema["properties"]
    assert schema["properties"]["search_type"]["description"] == _description(
        SearchTypeArg
    )


def test_agent_table_tool_exposes_configurable_row_limit() -> None:
    tool = explore_toolset.tools["query_table"]
    schema = tool.function_schema.json_schema

    assert tool.description == tool_description(QueryTableTool)
    assert schema["properties"]["row_limit"]["maximum"] == 1000
    assert schema["properties"]["row_limit"]["description"] == _description(
        TableRowLimitArg
    )


def test_agent_python_tool_describes_monty_constraints() -> None:
    tool = compute_toolset.tools["run_python"]
    schema = tool.function_schema.json_schema
    description = tool.description

    assert description == tool_description(RunPythonTool)
    assert description is not None
    assert "Monty interpreter" in description
    assert "subset of Python and its standard library" in description
    assert schema["properties"]["code"]["description"] == _description(CodeArg)
    assert schema["properties"]["script_path"]["description"] == _description(
        PythonScriptPathArg
    )
    assert schema["properties"]["input_paths"]["maxItems"] == 20
    assert schema["properties"]["input_paths"]["description"] == _description(
        PythonInputPathsArg
    )
    assert schema["properties"]["output_path"]["description"] == _description(
        PythonOutputPathArg
    )


async def test_mcp_tool_reuses_canonical_docstring_and_alias_metadata() -> None:
    tool = await mcp_app.get_tool("grep")
    assert tool is not None

    assert tool.description == tool_description(GrepTool)
    assert tool.parameters["properties"]["pattern"]["description"] == _description(
        GrepPatternArg
    )
    assert tool.parameters["properties"]["context"]["description"] == _description(
        GrepContextArg
    )


async def test_mcp_exposes_jq_named_by_the_document_reader() -> None:
    tool = await mcp_app.get_tool("jq")

    assert tool is not None


async def test_mcp_search_tool_uses_consistent_search_type_name() -> None:
    tool = await mcp_app.get_tool("search")
    assert tool is not None

    assert "search_type" in tool.parameters["properties"]
    assert "type" not in tool.parameters["properties"]
    assert tool.parameters["properties"]["search_type"]["description"] == _description(
        SearchTypeArg
    )
