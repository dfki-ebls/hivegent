"""Adapter utilities for registering Tool classes with FastMCP."""

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]
from fastmcp.tools.tool import ToolResult

from .base import CallInfo, Tool, ToolOutput, factory_tool_name

__all__ = ["for_fastmcp", "register_mcp_tools"]


def wrap_tool_output(result: ToolOutput[Any]) -> ToolResult:
    """Extract model-facing text from a :class:`ToolOutput`."""
    return ToolResult(content=result.text)


def for_fastmcp(
    factory_provider: Callable[..., Tool],
) -> Callable[..., Any]:
    """Build a wrapper function whose signature FastMCP can introspect.

    The tool class is inferred from *factory_provider*'s return type
    annotation.  The provider's unbound parameters with ``Depends``
    defaults are resolved by FastMCP at call time.

    Args:
        factory_provider: Callable that returns a Tool instance.
            Must have a return annotation that is a ``Tool`` subclass.

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    info = CallInfo.from_factory(factory_provider)

    # Append _tool_ as KEYWORD_ONLY with Depends default.
    tool_param = inspect.Parameter(
        "_tool_",
        inspect.Parameter.KEYWORD_ONLY,
        default=Depends(factory_provider),
        annotation=Any,
    )
    new_sig = inspect.Signature(
        parameters=[*info.params, tool_param],
        return_annotation=str,
    )

    new_annotations: dict[str, Any] = {
        "_tool_": Any,
        **info.annotations,
        "return": str,
    }

    if info.is_async:

        async def wrapper(**kwargs: Any) -> Any:  # noqa: ANN401
            return wrap_tool_output(await kwargs.pop("_tool_")(**kwargs))
    else:

        def wrapper(**kwargs: Any) -> Any:  # noqa: ANN401
            return wrap_tool_output(kwargs.pop("_tool_")(**kwargs))

    info.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_mcp_tools(
    app: FastMCP,
    factories: Sequence[Callable[..., Tool]],
) -> None:
    """Register multiple Tool factories on a FastMCP app.

    Each factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Args:
        app: The FastMCP application.
        factories: Sequence of factory callables.
    """
    for factory in factories:
        fn = for_fastmcp(factory)
        app.tool(
            fn,
            name=factory_tool_name(fn),
            description=fn.__doc__,
        )
