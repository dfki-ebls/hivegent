"""Unit tests for tool adapter utilities."""

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast, override

import pytest
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field
from pydantic_ai import FunctionToolset
from pydantic_ai.messages import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from hivegent.tools.base import (
    AsyncTool,
    SyncTool,
    ToolOutput,
    ToolSpec,
    tool_description,
)
from hivegent.tools.fastmcp import (
    for_fastmcp,
    register_mcp_tools,
)
from hivegent.tools.fastmcp import wrap_tool_output as wrap_fastmcp_output
from hivegent.tools.pydantic_ai import (
    for_pydantic_ai,
    register_agent_tools,
    wrap_tool_output,
)
from hivegent.tools.sink import OutputPathArg, RedirectedOutput, RedirectingTool

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
class GenericFixtureTool[R = str](AsyncTool[list[R]]):
    """A generic tool with a default result type."""

    @override
    async def __call__(self, value: R) -> ToolOutput[list[R]]:
        """Return one value."""
        return ToolOutput(data=[value])


@dataclass(slots=True, frozen=True)
class IntFixtureTool(GenericFixtureTool[int]):
    """A concrete subclass of a generic tool."""


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


def _generic_default(_d: _Deps) -> GenericFixtureTool:
    return GenericFixtureTool()


def _generic_subclass(_d: _Deps) -> IntFixtureTool:
    return IntFixtureTool()


class Hit(BaseModel):
    """One matched record."""

    label: str


@dataclass(slots=True, frozen=True)
class ModelTool(SyncTool[list[Hit]]):
    """A tool whose payload is a model, so its schema carries `$defs`."""

    @override
    def __call__(self, query: str) -> ToolOutput[list[Hit]]:
        """Find matching records."""
        return ToolOutput(data=[Hit(label=query)], formatted=query)


@dataclass(slots=True, frozen=True)
class RedirectingFixtureTool(RedirectingTool[list[str]]):
    """A tool offering the redirect, to check the branch goes with it."""

    @override
    async def __call__(
        self, query: str, output_path: OutputPathArg = None
    ) -> ToolOutput[list[str] | RedirectedOutput]:
        """Find matching records."""
        return ToolOutput(data=[query], formatted=query)


def _model_mcp() -> ModelTool:
    return ModelTool()


def _redirecting_mcp() -> RedirectingFixtureTool:
    return RedirectingFixtureTool(writer=None)


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


class TestToolSpec:
    """What every adapter is built from, and what it refuses to build from."""

    def test_the_result_type_is_the_bound_tool_output(self) -> None:
        """Concrete, so the sandbox can name the record its stub declares."""
        assert ToolSpec.from_factory(_tool_output_deps).data_type == list[str]

    @pytest.mark.parametrize(
        ("factory", "expected_data", "expected_value"),
        [
            (_generic_default, list[str], str),
            (_generic_subclass, list[int], int),
        ],
    )
    def test_generic_defaults_and_subclasses_bind_the_result_type(
        self,
        factory: Callable[[_Deps], GenericFixtureTool[str] | IntFixtureTool],
        expected_data: Any,
        expected_value: type[Any],
    ) -> None:
        spec = ToolSpec.from_factory(factory)

        assert spec.data_type == expected_data
        assert spec.annotations["value"] is expected_value
        assert spec.params[0].annotation is expected_value

    def test_a_call_that_does_not_return_tool_output_is_refused(self) -> None:
        """Caught at extraction, where every adapter shares the one message.

        The base classes declare the envelope, so this is reachable only by
        suppressing both checkers -- as a stand-in for the return annotation
        that never resolves, which used to surface in the sandbox rather than
        here at the tool that mis-declared itself.
        """

        @dataclass(slots=True, frozen=True)
        class _Bare(SyncTool[str]):
            """Return the payload without its envelope."""

            @override
            def __call__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]  # ty: ignore[invalid-method-override]
                return "no envelope"

        def _bare(_d: _Deps) -> _Bare:
            return _Bare()

        with pytest.raises(TypeError, match="must return ToolOutput"):
            _ = ToolSpec.from_factory(_bare)


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

    def test_tool_output_return_rewritten_to_tool_result(self) -> None:
        fn = for_fastmcp(_tool_output_mcp)
        assert fn.__annotations__["return"] is ToolResult
        assert inspect.signature(fn).return_annotation is ToolResult

    async def test_structured_output_matches_the_registered_schema(self) -> None:
        app = FastMCP("test")
        register_mcp_tools(app, [_tool_output_mcp])
        registered = await app.get_tool("tool_output_mcp")
        assert registered is not None

        result = wrap_fastmcp_output(
            ToolOutput(data=["match"], formatted="match"),
            ToolSpec.from_factory(_tool_output_mcp),
            wrap_data=True,
        )

        assert registered.output_schema == {
            "type": "object",
            "properties": {"result": {"items": {"type": "string"}, "type": "array"}},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert result.structured_content == {"result": ["match"]}
        assert result.meta == {"fastmcp": {"wrap_result": True}}

    async def test_a_model_payload_keeps_its_definitions_at_the_schema_root(
        self,
    ) -> None:
        """Where a `#/$defs/...` pointer resolves, so the refs are not dangling."""
        app = FastMCP("test")
        register_mcp_tools(app, [_model_mcp])
        registered = await app.get_tool("model_mcp")
        assert registered is not None
        schema = registered.output_schema
        assert schema is not None

        assert "$defs" in schema
        assert "Hit" in schema["$defs"]
        assert schema["properties"]["result"]["items"] == {"$ref": "#/$defs/Hit"}

    async def test_an_omitted_argument_takes_its_unreachable_branch_with_it(
        self,
    ) -> None:
        """The receipt is reachable only through `output_path`."""
        app = FastMCP("test")
        register_mcp_tools(app, [_redirecting_mcp], omit=(OutputPathArg,))
        registered = await app.get_tool("redirecting_mcp")
        assert registered is not None

        assert "output_path" not in registered.parameters["properties"]
        assert "RedirectedOutput" not in json.dumps(registered.output_schema)
