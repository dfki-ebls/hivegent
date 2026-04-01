"""Adapter utilities for registering Tool classes with pydantic-ai."""

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from .base import CallInfo, Tool, ToolOutput, factory_tool_name

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
    info = CallInfo.from_factory(factory)

    # Build parameter list: RunContext first, then __call__ params.
    ctx_annotation: Any = RunContext[deps_type]
    ctx_param = inspect.Parameter(
        "ctx",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=ctx_annotation,
    )
    new_sig = inspect.Signature(
        parameters=[ctx_param, *info.params],
        return_annotation=ToolReturn,
    )

    new_annotations: dict[str, Any] = {
        "ctx": ctx_annotation,
        **info.annotations,
        "return": ToolReturn,
    }

    if info.is_async:

        async def wrapper(ctx: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return wrap_tool_output(await factory(ctx.deps)(**kwargs))
    else:

        def wrapper(ctx: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return wrap_tool_output(factory(ctx.deps)(**kwargs))

    info.apply_to(wrapper, new_sig, new_annotations)
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
