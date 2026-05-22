"""Adapter utilities for registering Tool classes with pydantic-ai."""

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, cast

from pydantic import BeforeValidator
from pydantic_ai import BinaryContent, FunctionToolset, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from .base import BinaryAttachment, CallInfo, Tool, ToolOutput, factory_tool_name

__all__ = ["for_pydantic_ai", "register_agent_tools", "wrap_tool_output"]

DATA_CHUNK_TYPE = "data-tool-output"
"""DataChunk type used to stream structured tool data to the frontend."""


def _dequote_value(value: object) -> object:
    """Strip surrounding double quotes from a string value.

    llama.cpp's autoparser path does not constrain string content
    (notably ``enum`` literals) at generation time, so some local
    models emit arguments JSON-double-encoded — e.g. the value
    ``\"files_with_matches\"`` instead of ``files_with_matches``.
    Stripping bookend quotes before pydantic's strict validation lets
    such values match their declared schema.  Non-strings and strings
    without matching bookend quotes pass through unchanged.
    """
    if (
        isinstance(value, str)
        and len(value) >= 2
        and value[0] == '"'
        and value[-1] == '"'
    ):
        return value[1:-1]
    return value


_DEQUOTE_VALIDATOR = BeforeValidator(_dequote_value)


def _binary_attachment_to_content(att: BinaryAttachment) -> BinaryContent:
    """Convert a framework-neutral attachment to a pydantic-ai BinaryContent."""
    return BinaryContent(
        data=att.data,
        media_type=att.media_type,
        identifier=att.identifier,
    )


def wrap_tool_output(result: ToolOutput[Any]) -> ToolReturn:
    """Wrap a :class:`ToolOutput` in a :class:`ToolReturn`.

    ``return_value`` carries the compact text the LLM sees directly.
    When the tool produces binary ``attachments``, they are converted to
    pydantic-ai :class:`BinaryContent` and embedded *inline* in the tool
    return value (per the framework guidance: multimodal content sent
    natively in the tool result belongs in ``return_value``, not in the
    separate ``content`` channel which would surface as an extra user
    turn).

    Structured ``data`` (anything that isn't a plain string) is attached
    as a :class:`DataChunk` in ``metadata`` so the frontend receives it
    without parsing the LLM-facing text.  String-valued ``data`` is
    considered the canonical payload on its own and is left in
    ``return_value`` without duplication.
    """
    metadata: DataChunk | None = None
    if result.data is not None and not isinstance(result.data, str):
        metadata = DataChunk(type=DATA_CHUNK_TYPE, data=result.data)

    return_value: Any = result.text
    if result.attachments:
        return_value = [
            result.text,
            *(_binary_attachment_to_content(a) for a in result.attachments),
        ]
    return ToolReturn(return_value=return_value, metadata=metadata)


def for_pydantic_ai[D](
    factory: Callable[[D], Tool[Any]],
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
    # Use getattr to build RunContext[D] at runtime without a subscript
    # expression that ty would interpret as a static type form.
    ctx_annotation: Any = RunContext.__class_getitem__(deps_type)  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[unresolved-attribute]
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
        **{n: Annotated[h, _DEQUOTE_VALIDATOR] for n, h in info.annotations.items()},
        "return": ToolReturn,
    }

    if info.is_async:

        async def wrapper(ctx: Any, **kwargs: Any) -> Any:
            result = cast(Awaitable[ToolOutput[Any]], factory(ctx.deps)(**kwargs))
            return wrap_tool_output(await result)
    else:

        def wrapper(ctx: Any, **kwargs: Any) -> Any:
            result = cast(ToolOutput[Any], factory(ctx.deps)(**kwargs))
            return wrap_tool_output(result)

    info.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_agent_tools[D](
    toolset: FunctionToolset[D],
    deps_type: type[D],
    factories: Sequence[Callable[[D], Tool[Any]]],
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
