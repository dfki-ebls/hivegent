from pydantic import TypeAdapter

from hivegent.agents import explore_toolset
from hivegent.mcp import mcp_app
from hivegent.tools.base import tool_description
from hivegent.tools.documents import (
    DocumentMaxDepthArg,
    DocumentPathArg,
    ListDocumentsTool,
)
from hivegent.tools.grep import GrepContextArg, GrepPatternArg, GrepTool
from hivegent.tools.retrieval import SearchTypeArg


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


async def test_mcp_search_tool_uses_consistent_search_type_name() -> None:
    tool = await mcp_app.get_tool("search")
    assert tool is not None

    assert "search_type" in tool.parameters["properties"]
    assert "type" not in tool.parameters["properties"]
    assert tool.parameters["properties"]["search_type"]["description"] == _description(
        SearchTypeArg
    )
