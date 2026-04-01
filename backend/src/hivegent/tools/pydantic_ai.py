"""Adapter utilities for registering Tool classes with pydantic-ai."""

import inspect
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any, get_type_hints

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from .base import Tool, ToolOutput, factory_tool_name, resolve_tool_cls, tool_description

__all__ = ["for_pydantic_ai", "register_agent_tools", "wrap_tool_output"]

DATA_CHUNK_TYPE = "data-tool-output"
"""DataChunk type used to stream structured tool data to the frontend."""


def wrap_tool_output(result: ToolOutput[Any]) -> ToolReturn:
    """Wrap a :class:`ToolOutput` in a :class:`ToolReturn`.

    ``return_value`` carries the compact text the LLM sees directly.
    When ``data`` is structured (not a plain string or ``None``), a
    :class:`DataChunk` is attached as ``metadata`` so the Vercel AI
    stream delivers the structured payload to the frontend.
    """
    metadata: DataChunk | None = None
    if result.data is not None and not isinstance(result.data, str):
        metadata = DataChunk(type=DATA_CHUNK_TYPE, data=result.data)
    return ToolReturn(return_value=result.text, metadata=metadata)


def for_pydantic_ai[D](
    factory: Callable[[D], Tool],
    deps_type: type[D],
) -> Callable[..., Any]:
    """Build a wrapper function whose signature pydantic-ai can introspect.

    The tool class is inferred from *factory*'s return type annotation.

    Args:
        factory: Callable that receives deps and returns a Tool instance.
            Must have a return annotation that is a ``Tool`` subclass.
        deps_type: The RunContext deps type (e.g. ``UserDeps``).

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    tool_cls = resolve_tool_cls(factory)
    call = tool_cls.__call__
    is_async = inspect.iscoroutinefunction(call)
    sig = inspect.signature(call)
    hints = get_type_hints(call, include_extras=True)

    # Build new parameters: ctx first, then __call__ params minus 'self'
    ctx_annotation = RunContext[deps_type]  # type: ignore[valid-type]
    ctx_param = inspect.Parameter(
        "ctx",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=ctx_annotation,
    )
    call_params = [p for name, p in sig.parameters.items() if name != "self"]
    new_params = [ctx_param, *call_params]

    # The wrapper returns ToolReturn; rewrite the annotation so pydantic-ai
    # recognises the structured return and extracts return_value / metadata.
    ret = hints.get("return")
    ret_annotation = ToolReturn if isinstance(ret, type) and issubclass(ret, ToolOutput) else sig.return_annotation
    new_sig = sig.replace(parameters=new_params, return_annotation=ret_annotation)

    # Build annotations dict
    new_annotations: dict[str, Any] = {"ctx": ctx_annotation}
    for p in call_params:
        if p.name in hints:
            new_annotations[p.name] = hints[p.name]
    if ret is not None:
        new_annotations["return"] = ToolReturn if isinstance(ret, type) and issubclass(ret, ToolOutput) else ret

    if is_async:

        @wraps(call)
        async def wrapper(ctx: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return wrap_tool_output(await factory(ctx.deps)(**kwargs))
    else:

        @wraps(call)
        def wrapper(ctx: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return wrap_tool_output(factory(ctx.deps)(**kwargs))

    setattr(wrapper, "__signature__", new_sig)  # pyright: ignore[reportAttributeAccessIssue]
    wrapper.__annotations__ = new_annotations
    wrapper.__doc__ = tool_description(tool_cls)
    name = factory_tool_name(factory)
    wrapper.__name__ = name
    wrapper.__qualname__ = name
    # @wraps copies __wrapped__ from the original __call__, which
    # get_type_hints() follows to discover the return type.  Remove it so
    # consumers use our rewritten annotations instead.
    wrapper.__wrapped__ = None  # type: ignore[attr-defined]
    return wrapper


def register_agent_tools[D](
    toolset: FunctionToolset[D],
    deps_type: type[D],
    factories: Sequence[Callable[[D], Tool]],
    *,
    requires_approval: bool | None = None,
) -> None:
    """Register multiple Tool factories on a FunctionToolset.

    Each factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Args:
        toolset: The target toolset.
        deps_type: The RunContext deps type.
        factories: Sequence of factory callables.
        requires_approval: Whether tool calls need user approval.
    """
    for factory in factories:
        fn = for_pydantic_ai(factory, deps_type)
        toolset.add_function(
            fn,
            name=factory_tool_name(fn),
            description=fn.__doc__,
            requires_approval=requires_approval,
        )
