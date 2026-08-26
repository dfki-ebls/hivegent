"""Unit tests for tool adapter utilities."""

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast, override

from pydantic import Field
from pydantic_ai import FunctionToolset
from pydantic_ai.messages import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from hivegent.tools.base import AsyncTool, SyncTool, ToolOutput, tool_description
from hivegent.tools.fastmcp import for_fastmcp
from hivegent.tools.pydantic_ai import (
    for_pydantic_ai,
    register_agent_tools,
    wrap_tool_output,
)

# -- Fixtures ----------------------------------------------------------------

QueryArg = Annotated[str, Field(description="A search query.")]
LimitArg = Annotated[int, Field(description="Max results.", ge=1)]


@dataclass(slots=True, frozen=True)
class SyncFixtureTool(SyncTool[str]):
    """A sync tool for testing."""

    prefix: str = ""

    @override
    def __call__(self, query: QueryArg, limit: LimitArg = 5) -> ToolOutput[str]:
        """Search for things."""
        return ToolOutput(data=f"{self.prefix}{query}:{limit}")


@dataclass(slots=True, frozen=True)
class AsyncFixtureTool(AsyncTool[list[str]]):
    """An async tool for testing."""

    path: Path = Path(".")

    @override
    async def __call__(self, query: QueryArg) -> ToolOutput[list[str]]:
        """Fetch results asynchronously."""
        return ToolOutput(data=[str(self.path), query])


@dataclass(slots=True, frozen=True)
class ToolOutputTool(SyncTool[list[str]]):
    """A tool that returns ToolOutput with explicit formatted."""

    @override
    def __call__(self, query: QueryArg) -> ToolOutput[list[str]]:
        """Search with structured output."""
        results = [query]
        return ToolOutput(data=results, formatted=query)


@dataclass(slots=True, frozen=True)
class _Deps:
    value: str = "test"


def _sync_with_prefix(d: _Deps) -> SyncFixtureTool:
    return SyncFixtureTool(prefix=d.value)


def _sync_default(_d: _Deps) -> SyncFixtureTool:
    return SyncFixtureTool()


def _async_default(_d: _Deps) -> AsyncFixtureTool:
    return AsyncFixtureTool()


def _tool_output_deps(_d: _Deps) -> ToolOutputTool:
    return ToolOutputTool()


def _sync_mcp() -> SyncFixtureTool:
    return SyncFixtureTool()


def _async_mcp() -> AsyncFixtureTool:
    return AsyncFixtureTool()


def _tool_output_mcp() -> ToolOutputTool:
    return ToolOutputTool()


# -- for_pydantic_ai ---------------------------------------------------------


class TestForPydanticAI:
    """Tests for for_pydantic_ai."""

    def test_sync_wrapper_signature(self) -> None:
        fn = for_pydantic_ai(_sync_with_prefix, _Deps)
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert params[0] == "ctx"
        assert "query" in params
        assert "limit" in params

    def test_sync_wrapper_name(self) -> None:
        fn = for_pydantic_ai(_sync_default, _Deps)
        assert cast(Any, fn).__name__ == "sync_default"

    def test_sync_wrapper_doc(self) -> None:
        fn = for_pydantic_ai(_sync_default, _Deps)
        assert fn.__doc__ == tool_description(SyncFixtureTool)

    def test_async_wrapper_is_coroutine(self) -> None:
        fn = for_pydantic_ai(_async_default, _Deps)
        assert inspect.iscoroutinefunction(fn)

    def test_async_wrapper_name(self) -> None:
        fn = for_pydantic_ai(_async_default, _Deps)
        assert cast(Any, fn).__name__ == "async_default"

    def test_annotations_include_return(self) -> None:
        fn = for_pydantic_ai(_sync_default, _Deps)
        assert "return" in fn.__annotations__

    def test_tool_output_return_rewritten_to_tool_return(self) -> None:
        fn = for_pydantic_ai(_tool_output_deps, _Deps)
        assert fn.__annotations__["return"] is ToolReturn
        assert inspect.signature(fn).return_annotation is ToolReturn


# -- register_agent_tools -----------------------------------------------------


class TestRegisterAgentTools:
    """Tests for register_agent_tools."""

    def test_registers_tools(self) -> None:
        toolset: FunctionToolset[_Deps] = FunctionToolset()
        register_agent_tools(
            toolset,
            _Deps,
            [
                _sync_default,
                _async_default,
            ],
        )
        assert "sync_default" in toolset.tools
        assert "async_default" in toolset.tools


# -- wrap_tool_output ---------------------------------------------------------


class TestWrapToolOutput:
    """Tests for wrap_tool_output structured-data correlation."""

    def test_structured_data_chunk_carries_tool_call_id(self) -> None:
        # The frontend correlates the data part with its tool part by id,
        # since the AI SDK appends data parts out of positional order.
        out = wrap_tool_output(
            ToolOutput(data=[1, 2, 3], formatted="three"),
            tool_call_id="call-xyz",
        )
        assert isinstance(out.metadata, DataChunk)
        assert out.metadata.id == "call-xyz"
        assert out.metadata.data == [1, 2, 3]
        assert out.return_value == "three"

    def test_string_data_emits_no_metadata_chunk(self) -> None:
        out = wrap_tool_output(ToolOutput(data="plain"), tool_call_id="call-1")
        assert out.metadata is None
        assert out.return_value == "plain"


# -- for_fastmcp --------------------------------------------------------------


class TestForFastMCP:
    """Tests for for_fastmcp."""

    def test_appends_tool_param(self) -> None:
        fn = for_fastmcp(_sync_mcp)
        sig = inspect.signature(fn)
        assert "_tool_" in sig.parameters
        param = sig.parameters["_tool_"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_preserves_call_params(self) -> None:
        fn = for_fastmcp(_sync_mcp)
        sig = inspect.signature(fn)
        assert "query" in sig.parameters
        assert "limit" in sig.parameters

    def test_name(self) -> None:
        fn = for_fastmcp(_sync_mcp)
        assert cast(Any, fn).__name__ == "sync_mcp"

    def test_doc(self) -> None:
        fn = for_fastmcp(_sync_mcp)
        assert fn.__doc__ == tool_description(SyncFixtureTool)

    def test_async_wrapper(self) -> None:
        fn = for_fastmcp(_async_mcp)
        assert inspect.iscoroutinefunction(fn)

    def test_tool_output_return_rewritten_to_str(self) -> None:
        fn = for_fastmcp(_tool_output_mcp)
        assert fn.__annotations__["return"] is str
        assert inspect.signature(fn).return_annotation is str
