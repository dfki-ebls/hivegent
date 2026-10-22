"""Tests proving adapter-built tools produce identical schemas to decorator-built ones."""

from dataclasses import dataclass
from typing import Annotated, Any, override

import pytest
from fastmcp import FastMCP
from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext

from hivegent.tools.base import AsyncTool, SyncTool, ToolOutput
from hivegent.tools.fastmcp import register_mcp_tools
from hivegent.tools.pydantic_ai import register_agent_tools

# -- Annotated arg aliases ---------------------------------------------------

NameArg = Annotated[str, Field(description="Full name of the entity to look up.")]
CountArg = Annotated[int, Field(description="Number of items to return.", ge=1, le=100)]
CategoryArg = Annotated[str | None, Field(description="Optional category filter.")]
VerboseArg = Annotated[bool, Field(description="Whether to include detailed output.")]

# -- Toy tool dataclasses ----------------------------------------------------


@dataclass(slots=True, frozen=True)
class SyncLookupTool(SyncTool[str]):
    """Sync lookup tool for testing."""

    prefix: str = ""

    @override
    def __call__(
        self,
        name: NameArg,
        count: CountArg = 10,
        category: CategoryArg = None,
        verbose: VerboseArg = False,
    ) -> ToolOutput[str]:
        """Look up entities by name."""
        return ToolOutput(data=f"{self.prefix}{name}:{count}")


@dataclass(slots=True, frozen=True)
class AsyncLookupTool(AsyncTool[str]):
    """Async lookup tool for testing."""

    prefix: str = ""

    @override
    async def __call__(
        self,
        name: NameArg,
        count: CountArg = 10,
        category: CategoryArg = None,
        verbose: VerboseArg = False,
    ) -> ToolOutput[str]:
        """Look up entities by name asynchronously."""
        return ToolOutput(data=f"{self.prefix}{name}:{count}")


# -- Deps and factory functions ----------------------------------------------


@dataclass(slots=True, frozen=True)
class _Deps:
    prefix: str = ""


def _sync_lookup() -> SyncLookupTool:
    return SyncLookupTool()


def _async_lookup() -> AsyncLookupTool:
    return AsyncLookupTool()


def _sync_lookup_deps(d: _Deps) -> SyncLookupTool:
    return SyncLookupTool(prefix=d.prefix)


def _async_lookup_deps(d: _Deps) -> AsyncLookupTool:
    return AsyncLookupTool(prefix=d.prefix)


# -- Shared evaluation infrastructure ----------------------------------------


@dataclass(slots=True, frozen=True)
class PublicToolSnapshot:
    """Framework-agnostic snapshot of a tool's public metadata."""

    name: str
    description: str | None
    parameters: dict[str, Any]


def fastmcp_spec(tool: Any) -> PublicToolSnapshot:
    """Extract public metadata from a FastMCP Tool object."""
    return PublicToolSnapshot(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
    )


def pydantic_ai_spec(tool: Any) -> PublicToolSnapshot:
    """Extract public metadata from a pydantic-ai Tool object."""
    return PublicToolSnapshot(
        name=tool.name,
        description=tool.description,
        parameters=tool.function_schema.json_schema,
    )


def assert_specs_equal(adapter: PublicToolSnapshot, direct: PublicToolSnapshot) -> None:
    """Assert two public snapshots are identical."""
    assert adapter.name == direct.name, (
        f"name mismatch: {adapter.name!r} != {direct.name!r}"
    )
    assert adapter.description == direct.description, (
        f"description mismatch:\n  adapter: {adapter.description!r}\n  direct:  {direct.description!r}"
    )

    # Per-parameter description check for clear diagnostics
    adapter_props = adapter.parameters.get("properties", {})
    direct_props = direct.parameters.get("properties", {})
    all_params = set(adapter_props) | set(direct_props)
    for param in sorted(all_params):
        a_desc = adapter_props.get(param, {}).get("description")
        d_desc = direct_props.get(param, {}).get("description")
        assert a_desc == d_desc, (
            f"description mismatch for parameter {param!r}: {a_desc!r} != {d_desc!r}"
        )

    # Full schema equality
    assert adapter.parameters == direct.parameters


# -- FastMCP tests -----------------------------------------------------------


def _make_adapter_fastmcp_app() -> FastMCP:
    app = FastMCP("adapter")
    register_mcp_tools(app, [_sync_lookup, _async_lookup])
    return app


def _make_direct_fastmcp_app() -> FastMCP:
    app = FastMCP("direct")

    @app.tool
    def sync_lookup(
        name: NameArg,
        count: CountArg = 10,
        category: CategoryArg = None,
        verbose: VerboseArg = False,
    ) -> str:
        """Look up entities by name."""
        return name

    @app.tool
    async def async_lookup(
        name: NameArg,
        count: CountArg = 10,
        category: CategoryArg = None,
        verbose: VerboseArg = False,
    ) -> str:
        """Look up entities by name asynchronously."""
        return name

    return app


class TestFastMCPSchemaEquivalence:
    """Adapter-registered FastMCP tools match decorator-registered ones."""

    @pytest.fixture
    def adapter_app(self) -> FastMCP:
        return _make_adapter_fastmcp_app()

    @pytest.fixture
    def direct_app(self) -> FastMCP:
        return _make_direct_fastmcp_app()

    async def test_sync_tool_matches(
        self, adapter_app: FastMCP, direct_app: FastMCP
    ) -> None:
        adapter_tool = await adapter_app.get_tool("sync_lookup")
        direct_tool = await direct_app.get_tool("sync_lookup")
        assert adapter_tool is not None
        assert direct_tool is not None
        assert_specs_equal(fastmcp_spec(adapter_tool), fastmcp_spec(direct_tool))
        assert adapter_tool.output_schema == direct_tool.output_schema

    async def test_async_tool_matches(
        self, adapter_app: FastMCP, direct_app: FastMCP
    ) -> None:
        adapter_tool = await adapter_app.get_tool("async_lookup")
        direct_tool = await direct_app.get_tool("async_lookup")
        assert adapter_tool is not None
        assert direct_tool is not None
        assert_specs_equal(fastmcp_spec(adapter_tool), fastmcp_spec(direct_tool))
        assert adapter_tool.output_schema == direct_tool.output_schema


# -- Pydantic AI tests -------------------------------------------------------


def _make_adapter_pydantic_ai_toolset() -> FunctionToolset[_Deps]:
    toolset: FunctionToolset[_Deps] = FunctionToolset()
    register_agent_tools(toolset, _Deps, [_sync_lookup_deps, _async_lookup_deps])
    return toolset


def _make_direct_pydantic_ai_toolset() -> FunctionToolset[_Deps]:
    toolset: FunctionToolset[_Deps] = FunctionToolset()

    @toolset.tool
    def sync_lookup_deps(
        ctx: RunContext[_Deps],
        name: NameArg,
        count: CountArg = 10,
        category: CategoryArg = None,
        verbose: VerboseArg = False,
    ) -> str:
        """Look up entities by name."""
        return name

    @toolset.tool
    async def async_lookup_deps(
        ctx: RunContext[_Deps],
        name: NameArg,
        count: CountArg = 10,
        category: CategoryArg = None,
        verbose: VerboseArg = False,
    ) -> str:
        """Look up entities by name asynchronously."""
        return name

    return toolset


class TestPydanticAISchemaEquivalence:
    """Adapter-registered pydantic-ai tools match decorator-registered ones."""

    @pytest.fixture
    def adapter_toolset(self) -> FunctionToolset[_Deps]:
        return _make_adapter_pydantic_ai_toolset()

    @pytest.fixture
    def direct_toolset(self) -> FunctionToolset[_Deps]:
        return _make_direct_pydantic_ai_toolset()

    def test_sync_tool_matches(
        self,
        adapter_toolset: FunctionToolset[_Deps],
        direct_toolset: FunctionToolset[_Deps],
    ) -> None:
        adapter_tool = adapter_toolset.tools["sync_lookup_deps"]
        direct_tool = direct_toolset.tools["sync_lookup_deps"]
        assert_specs_equal(
            pydantic_ai_spec(adapter_tool), pydantic_ai_spec(direct_tool)
        )

    def test_async_tool_matches(
        self,
        adapter_toolset: FunctionToolset[_Deps],
        direct_toolset: FunctionToolset[_Deps],
    ) -> None:
        adapter_tool = adapter_toolset.tools["async_lookup_deps"]
        direct_tool = direct_toolset.tools["async_lookup_deps"]
        assert_specs_equal(
            pydantic_ai_spec(adapter_tool), pydantic_ai_spec(direct_tool)
        )
