"""Adapter utilities for registering Tool classes with FastMCP."""

import inspect
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any, get_type_hints

from fastmcp import FastMCP
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from .base import Tool, factory_tool_name, resolve_tool_cls, tool_description

__all__ = ["for_fastmcp", "register_mcp_tools"]


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
    tool_cls = resolve_tool_cls(factory_provider)
    call = tool_cls.__call__
    is_async = inspect.iscoroutinefunction(call)
    sig = inspect.signature(call)
    hints = get_type_hints(call, include_extras=True)

    # __call__ params minus 'self'
    call_params = [p for name, p in sig.parameters.items() if name != "self"]

    # Append _tool_ as KEYWORD_ONLY with Depends default
    tool_param = inspect.Parameter(
        "_tool_",
        inspect.Parameter.KEYWORD_ONLY,
        default=Depends(factory_provider),
        annotation=Any,
    )
    new_params = [*call_params, tool_param]
    new_sig = sig.replace(parameters=new_params)

    # Build annotations
    new_annotations: dict[str, Any] = {"_tool_": Any}
    for p in call_params:
        if p.name in hints:
            new_annotations[p.name] = hints[p.name]
    if "return" in hints:
        new_annotations["return"] = hints["return"]

    if is_async:

        @wraps(call)
        async def wrapper(**kwargs: Any) -> Any:  # noqa: ANN401
            tool = kwargs.pop("_tool_")
            return await tool(**kwargs)
    else:

        @wraps(call)
        def wrapper(**kwargs: Any) -> Any:  # noqa: ANN401
            tool = kwargs.pop("_tool_")
            return tool(**kwargs)

    setattr(wrapper, "__signature__", new_sig)  # pyright: ignore[reportAttributeAccessIssue]
    wrapper.__annotations__ = new_annotations
    wrapper.__doc__ = tool_description(tool_cls)
    name = factory_tool_name(factory_provider)
    wrapper.__name__ = name
    wrapper.__qualname__ = name
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
