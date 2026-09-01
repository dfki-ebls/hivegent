"""Adapter utilities for registering Tool classes with pydantic-ai."""

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, cast

from pydantic import BeforeValidator
from pydantic_ai import BinaryContent, FunctionToolset, RunContext
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolReturn
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ArgsValidatorFunc
from pydantic_ai.tools import Tool as PydanticTool
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_ai.usage import RunUsage

from .base import (
    BinaryAttachment,
    Tool,
    ToolOutput,
    ToolSpec,
    factory_tool_name,
    translate_tool_retry,
)

__all__ = [
    "capability_tools",
    "for_pydantic_ai",
    "invoke_tool",
    "register_agent_tool",
    "register_agent_tools",
    "unwrap_tool_output",
    "wrap_tool_output",
]

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


def wrap_tool_output(
    result: ToolOutput[Any], *, tool_call_id: str | None = None
) -> ToolReturn:
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

    The chunk's ``id`` is stamped with ``tool_call_id`` so the frontend
    can correlate it with the originating tool part.  The Vercel AI SDK
    appends ``data-*`` parts to the end of ``message.parts`` rather than
    next to their tool part, so for parallel tool calls positional
    adjacency is lost — the id is the only reliable link.
    """
    metadata: DataChunk | None = None
    if result.data is not None and not isinstance(result.data, str):
        metadata = DataChunk(type=DATA_CHUNK_TYPE, id=tool_call_id, data=result.data)

    return_value: Any = result.text
    if result.attachments:
        return_value = [
            result.text,
            *(_binary_attachment_to_content(a) for a in result.attachments),
        ]
    return ToolReturn(return_value=return_value, metadata=metadata)


def unwrap_tool_output(result: Any) -> tuple[str | None, Any]:
    """Recover ``(text, structured_data)`` from a tool call return.

    The inverse of :func:`wrap_tool_output`: it reads back the LLM-facing
    text from ``return_value`` (a plain string, or the first element when
    binary attachments follow it) and the structured payload from the
    :class:`DataChunk` in ``metadata``.  Tools that bypass the wrapper and
    return a plain string or value are passed through unchanged.
    """
    if isinstance(result, ToolReturn):
        rv = result.return_value
        if isinstance(rv, str):
            text = rv
        elif isinstance(rv, list) and rv and isinstance(rv[0], str):
            text = rv[0]
        else:
            text = None
        data = result.metadata.data if isinstance(result.metadata, DataChunk) else None
        return text, data
    if isinstance(result, str):
        return result, None
    return None, result


def for_pydantic_ai[D](
    factory: Callable[[D], Tool[Any]],
    deps_type: type[D],
) -> Callable[..., ToolReturn] | Callable[..., Awaitable[ToolReturn]]:
    """Build a wrapper function whose signature pydantic-ai can introspect.

    The tool class is inferred from *factory*'s return type annotation.

    Args:
        factory: Callable that receives deps and returns a Tool instance.
            Must have a return annotation that is a ``Tool`` subclass.
        deps_type: The RunContext deps type (e.g. ``UserDeps``).

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    spec = ToolSpec.from_factory(factory)

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
        parameters=[ctx_param, *spec.params],
        return_annotation=ToolReturn,
    )

    new_annotations: dict[str, Any] = {
        "ctx": ctx_annotation,
        **{n: Annotated[h, _DEQUOTE_VALIDATOR] for n, h in spec.annotations.items()},
        "return": ToolReturn,
    }

    # Surface a ToolRetry as pydantic-ai's ModelRetry so the model can fix its
    # input and retry (any other exception would abort the whole run).
    if spec.is_async:

        async def wrapper(ctx: RunContext[D], **kwargs: Any) -> ToolReturn:
            with translate_tool_retry(ModelRetry):
                result = cast(Awaitable[ToolOutput[Any]], factory(ctx.deps)(**kwargs))
                return wrap_tool_output(await result, tool_call_id=ctx.tool_call_id)
    else:

        def wrapper(ctx: RunContext[D], **kwargs: Any) -> ToolReturn:
            with translate_tool_retry(ModelRetry):
                result = cast(ToolOutput[Any], factory(ctx.deps)(**kwargs))
                return wrap_tool_output(result, tool_call_id=ctx.tool_call_id)

    spec.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_agent_tool[D](
    toolset: FunctionToolset[D],
    deps_type: type[D],
    factory: Callable[[D], Tool[Any]],
    *,
    args_validator: ArgsValidatorFunc[D, ...] | None = None,
) -> None:
    """Register one Tool factory on a FunctionToolset.

    The factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Approval is not offered here: pydantic-ai's registration-time gate marks
    every call of a tool as needing a human, while what a run has to ask about
    is a property of the arguments one call carries, so *args_validator* is the
    only gate a tool registered through this seam has.

    Args:
        toolset: The target toolset.
        deps_type: The RunContext deps type.
        factory: Factory callable for the tool.
        args_validator: Optional validator for this tool's arguments.
    """
    fn = for_pydantic_ai(factory, deps_type)
    toolset.add_function(
        fn,
        name=factory_tool_name(fn),
        description=fn.__doc__,
        args_validator=args_validator,
    )


def register_agent_tools[D](
    toolset: FunctionToolset[D],
    deps_type: type[D],
    factories: Sequence[Callable[[D], Tool[Any]]],
    *,
    args_validator: ArgsValidatorFunc[D, ...] | None = None,
) -> None:
    """Register multiple Tool factories on a FunctionToolset.

    Each factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Args:
        toolset: The target toolset.
        deps_type: The RunContext deps type.
        factories: Sequence of factory callables.
        args_validator: Optional validator applied to every tool's arguments.
    """
    for factory in factories:
        register_agent_tool(
            toolset,
            deps_type,
            factory,
            args_validator=args_validator,
        )


def capability_tools[D](capability: Capability[D]) -> dict[str, PydanticTool[D]]:
    """Map every function-tool name in a capability's toolsets to its tool.

    The mechanical inverse of composing a capability: walk its function
    toolsets so callers (e.g. the debug/meta REST surface) can list or invoke
    individual tools straight from the same capability the agent is built from.
    """
    return {
        name: tool
        for toolset in capability.toolsets
        if isinstance(toolset, FunctionToolset)
        for name, tool in toolset.tools.items()
    }


async def invoke_tool[D](
    tool: PydanticTool[D], args: dict[str, Any], deps: D
) -> tuple[str | None, Any]:
    """Validate ``args`` against ``tool``'s schema, run it, and unwrap the result.

    Runs the exact code path the agent uses, so stateful behaviour (e.g.
    pgvector retrieval) is exercised identically.  The placeholder model on the
    run context is never invoked: every tool that talks to an LLM builds its own
    model from ``deps`` or settings.

    Args:
        tool: The pydantic-ai tool to run.
        args: Raw argument mapping, validated against the tool's schema.
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).

    Returns:
        A ``(text, structured_data)`` pair.

    Raises:
        pydantic.ValidationError: If ``args`` fail the tool's schema.
    """
    schema = tool.function_schema
    validated = schema.validator.validate_python(args)
    ctx = RunContext(
        deps=deps, model=TestModel(), usage=RunUsage(), tool_name=tool.name
    )
    return unwrap_tool_output(await schema.call(validated, ctx))
