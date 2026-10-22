"""Adapter utilities for registering Tool classes with pydantic-ai."""

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast, override

from pydantic import Field, PrivateAttr
from pydantic_ai import BinaryContent, FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart, ToolReturn
from pydantic_ai.models.test import TestModel
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import ArgsValidatorFunc, ToolPrepareFunc
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_core import to_json

from .base import (
    BinaryAttachment,
    Tool,
    ToolFactory,
    ToolOutput,
    ToolSpec,
    factory_tool_name,
    lenient,
    translate_tool_retry,
)

__all__ = [
    "ToolOutputChunk",
    "UnwrappedOutput",
    "for_pydantic_ai",
    "invoke_tool",
    "register_agent_tool",
    "register_agent_tools",
    "run_context",
    "unwrap_tool_output",
    "wrap_tool_output",
]

DATA_CHUNK_TYPE = "data-tool-output"
"""DataChunk type used to stream structured tool data to the frontend."""


def _binary_attachment_to_content(att: BinaryAttachment) -> BinaryContent:
    """Convert a framework-neutral attachment to a pydantic-ai BinaryContent."""
    return BinaryContent(
        data=att.data,
        media_type=att.media_type,
        identifier=att.identifier,
    )


def _encode(data: object) -> bytes:
    """*data* as JSON, the way a streamed chunk spells it."""
    return to_json(data, by_alias=True, exclude_none=True)


class ToolOutputChunk(DataChunk):
    """The structured payload of a tool return, as the frontend receives it.

    ``display_only`` carries :attr:`ToolOutput.display_only` to the run's
    capabilities and is excluded from what is streamed and stored.

    The data is encoded once, by :meth:`data_json`, and that encoding is
    what both the output spill measures and saves and the stream sends.
    """

    display_only: bool = Field(default=False, exclude=True)
    _json: bytes | None = PrivateAttr(default=None)

    def data_json(self) -> bytes:
        """The data as JSON, encoded on the first call and kept."""
        if self._json is None:
            self._json = _encode(self.data)

        return self._json

    @override
    def encode(self, sdk_version: int) -> str:
        """The streamed chunk, built around the one encoding of its data."""
        head = self.model_dump_json(by_alias=True, exclude_none=True, exclude={"data"})

        return f'{head[:-1]},"data":{self.data_json().decode()}}}'


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
    as a :class:`ToolOutputChunk` in ``metadata`` so the frontend receives it
    without parsing the LLM-facing text.  String-valued ``data`` is
    considered the canonical payload on its own and is left in
    ``return_value`` without duplication.

    The chunk's ``id`` is stamped with ``tool_call_id`` so the frontend
    can correlate it with the originating tool part.  The Vercel AI SDK
    appends ``data-*`` parts to the end of ``message.parts`` rather than
    next to their tool part, so for parallel tool calls positional
    adjacency is lost, and the id is the only reliable link.
    """
    metadata: ToolOutputChunk | None = None
    if result.data is not None and not isinstance(result.data, str):
        metadata = ToolOutputChunk(
            type=DATA_CHUNK_TYPE,
            id=tool_call_id,
            data=result.data,
            display_only=result.display_only,
        )

    return_value: Any = result.text
    if result.attachments:
        return_value = [
            result.text,
            *(_binary_attachment_to_content(a) for a in result.attachments),
        ]
    return ToolReturn(return_value=return_value, metadata=metadata)


@dataclass(frozen=True, slots=True)
class UnwrappedOutput:
    """A tool call return taken apart into what the model reads and what it carries."""

    text: str | None
    """The text the return value leads with, ``None`` when it has none."""

    rest: list[Any] | None
    """The items after :attr:`text` in a content list, ``None`` for a bare text."""

    data: Any
    """The structured payload, or a return value that is not text at all."""

    display_only: bool = False
    """Whether :attr:`data` is only what the client shows (:attr:`ToolOutput.display_only`)."""


def unwrap_tool_output(result: Any) -> UnwrappedOutput:
    """Take a tool call return apart, the inverse of :func:`wrap_tool_output`.

    The text is a plain string return value or the leading string of a content
    list such as ``[text, BinaryContent]``, and the structured payload is the
    :class:`DataChunk` in ``metadata``.  Tools that bypass the wrapper and
    return a plain string or value are read the same way.

    >>> unwrap_tool_output(["a", 1])
    UnwrappedOutput(text='a', rest=[1], data=None, display_only=False)
    """
    wrapped = isinstance(result, ToolReturn)
    value = result.return_value if wrapped else result
    chunk = (
        result.metadata if wrapped and isinstance(result.metadata, DataChunk) else None
    )
    data = chunk.data if chunk is not None else None
    display_only = isinstance(chunk, ToolOutputChunk) and chunk.display_only

    if isinstance(value, str):
        return UnwrappedOutput(value, None, data, display_only)

    if isinstance(value, list) and value and isinstance(value[0], str):
        return UnwrappedOutput(value[0], value[1:], data, display_only)

    return UnwrappedOutput(None, None, data if wrapped else value, display_only)


def for_pydantic_ai[D](
    factory: Callable[..., Tool[Any]],
    deps_type: type[D],
    *,
    takes_ctx: bool = False,
) -> Callable[..., ToolReturn] | Callable[..., Awaitable[ToolReturn]]:
    """Build a wrapper function whose signature pydantic-ai can introspect.

    The tool class is inferred from *factory*'s return type annotation.

    Args:
        factory: Callable that returns a Tool instance, built per call.
            Must have a return annotation that is a ``Tool`` subclass.
        deps_type: The RunContext deps type (e.g. ``UserDeps``).
        takes_ctx: Whether *factory* is handed the whole ``RunContext``, for
            a tool that spends what the run owns, such as its ``usage``,
            rather than only its deps, as pydantic-ai's own flag means.

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    spec = ToolSpec.from_factory(factory)

    def build(ctx: RunContext[D]) -> Tool[Any]:
        return factory(ctx) if takes_ctx else factory(ctx.deps)

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
        **{name: lenient(hint) for name, hint in spec.annotations.items()},
        "return": ToolReturn,
    }

    # Surface a ToolRetry as pydantic-ai's ModelRetry so the model can fix its
    # input and retry (any other exception would abort the whole run).
    if spec.is_async:

        async def wrapper(ctx: RunContext[D], **kwargs: Any) -> ToolReturn:
            with translate_tool_retry(ModelRetry):
                result = cast(Awaitable[ToolOutput[Any]], build(ctx)(**kwargs))
                return wrap_tool_output(await result, tool_call_id=ctx.tool_call_id)
    else:

        def wrapper(ctx: RunContext[D], **kwargs: Any) -> ToolReturn:
            with translate_tool_retry(ModelRetry):
                result = cast(ToolOutput[Any], build(ctx)(**kwargs))
                return wrap_tool_output(result, tool_call_id=ctx.tool_call_id)

    spec.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_agent_tool[D](
    toolset: FunctionToolset[D],
    deps_type: type[D],
    factory: Callable[..., Tool[Any]],
    *,
    takes_ctx: bool = False,
    args_validator: ArgsValidatorFunc[D, ...] | None = None,
    prepare: ToolPrepareFunc[D] | None = None,
    timeout: float | None = None,
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
        takes_ctx: Whether *factory* is handed the run rather than its deps.
        args_validator: Optional validator for this tool's arguments.
        prepare: Optional per-run hook that may withhold the tool.
        timeout: This tool's timeout, the toolset's for ``None``.
    """
    fn = for_pydantic_ai(factory, deps_type, takes_ctx=takes_ctx)
    toolset.add_function(
        fn,
        name=factory_tool_name(fn),
        description=fn.__doc__,
        args_validator=args_validator,
        prepare=prepare,
        timeout=timeout,
    )


def register_agent_tools[D](
    toolset: FunctionToolset[D],
    deps_type: type[D],
    factories: Sequence[ToolFactory[D]],
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


def run_context[D](
    deps: D, *, usage_limits: UsageLimits | None = None
) -> RunContext[D]:
    """The context a tool is prepared and called with outside an agent run.

    The placeholder model is never invoked: every tool that talks to an LLM
    builds its own model from ``deps`` or settings.

    Args:
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).
        usage_limits: The limits a subagent the tool runs is held to.
    """
    return RunContext(
        deps=deps, model=TestModel(), usage=RunUsage(), usage_limits=usage_limits
    )


async def invoke_tool[D](
    toolset: FunctionToolset[D],
    name: str,
    args: Mapping[str, Any],
    deps: D,
    *,
    approved: bool = False,
    usage_limits: UsageLimits | None = None,
) -> Any:
    """Run the tool *name* of *toolset* as an agent run calls it, outside of one.

    Goes through pydantic-ai's own :class:`ToolManager` over a toolset of
    the tool alone, which keeps the timeout and retries of the one it belongs
    to, so its ``prepare`` hook, its schema, its ``args_validator``, and its
    timeout apply exactly as in a run, with errors raised rather than turned
    into retry prompts, and no other tool's ``prepare`` hook runs.

    Args:
        toolset: The toolset holding the tool.
        name: The tool to run.
        args: Raw argument mapping, validated against the tool's schema.
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).
        approved: Whether the user already approved this call.
        usage_limits: The limits a subagent the tool runs is held to.

    Returns:
        The raw return value, taken apart by :func:`unwrap_tool_output`.

    Raises:
        KeyError: If *toolset* holds no tool *name*.
        pydantic.ValidationError: If ``args`` fail the tool's schema.
        ModelRetry: If the tool or its validator refuses the arguments, or
            its ``prepare`` hook withholds it.
        ApprovalRequired: If the validator asks the user first.
    """
    context = run_context(deps, usage_limits=usage_limits)
    alone = FunctionToolset[D](
        [toolset.tools[name]],
        max_retries=toolset.max_retries,
        timeout=toolset.timeout,
    )
    manager = await ToolManager(alone).for_run_step(context)

    return await manager.handle_call(
        ToolCallPart(name, dict(args)),
        approved=approved,
        wrap_validation_errors=False,
    )
