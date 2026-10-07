"""Adapter utilities for registering Tool classes with FastMCP."""

import base64
import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import quote

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool as FastMCPTool
from fastmcp.tools import ToolResult
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
)

from .base import (
    BinaryAttachment,
    Tool,
    ToolOutput,
    ToolSpec,
    accept_scalar,
    factory_tool_name,
    translate_tool_retry,
)
from .formatting import truncate_middle

__all__ = ["for_fastmcp", "output_schema", "register_mcp_tools", "wrap_tool_output"]


def _attachment_to_block(att: BinaryAttachment) -> ContentBlock:
    """Map a framework-neutral attachment to an MCP content block."""
    encoded = base64.b64encode(att.data).decode("ascii")
    if att.media_type.startswith("image/"):
        return ImageContent(
            type="image",
            data=encoded,
            mime_type=att.media_type,
        )
    # Percent-encode the identifier so pydantic AnyUrl doesn't silently
    # collapse `..` segments or reinterpret `?`/`#`/space.
    uri = f"hivegent://attachment/{quote(att.identifier or 'blob', safe='')}"
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=uri,  # pyright: ignore[reportArgumentType]
            mime_type=att.media_type,
            blob=encoded,
        ),
    )


def output_schema(data_type: Any) -> dict[str, Any] | None:
    """The MCP output schema FastMCP derives for a tool's structured payload.

    Asked of FastMCP rather than assembled here.  MCP requires an object at the
    top, so a payload that is not one has to be wrapped, and the wrapping is a
    convention with three parts: the ``result`` property, the
    ``x-fastmcp-wrap-result`` marker :meth:`FunctionTool.convert_result` reads
    back, and a ``$defs`` block hoisted to the document root because that is
    where the ``#/$defs/...`` pointers inside it resolve.  Assembling that by
    hand nested the definitions one level down and left every pointer dangling,
    so the question goes to the one place that owns the answer.

    The probe carries the annotation and nothing else, since the wrapper this
    schema describes returns a :class:`ToolResult`, which is the very type
    FastMCP suppresses schema generation for.
    """

    def probe() -> Any: ...

    probe.__annotations__ = {"return": data_type}

    return FastMCPTool.from_function(probe).output_schema


def wrap_tool_output(
    result: ToolOutput[Any],
    spec: ToolSpec,
    *,
    wrap_data: bool,
    max_chars: int | None = None,
) -> ToolResult:
    """Convert a :class:`ToolOutput` into an MCP :class:`ToolResult`.

    Text is always emitted as a :class:`TextContent` block, its middle left
    out past *max_chars* as an agent's tool return is, and binary attachments
    follow as image/audio/resource blocks.  An MCP client has no ``/tmp`` of
    this server's to read the rest from, so the structured content is what
    stays whole.

    The structured payload is serialised through the spec's own adapter, so it
    matches the schema registered for the tool, and is wrapped exactly when
    that schema says it is.  Returning a built :class:`ToolResult` is what
    keeps the text and the attachments, which FastMCP would otherwise replace
    with a rendering of the structured content, so the wrapping it does for a
    raw return value is mirrored here rather than inherited.
    """
    text = result.text if max_chars is None else truncate_middle(result.text, max_chars)
    blocks: list[ContentBlock] = [TextContent(type="text", text=text)]
    blocks.extend(_attachment_to_block(att) for att in result.attachments)
    data = spec.serialize_data(result.data)

    return ToolResult(
        content=blocks,
        structured_content={"result": data} if wrap_data else data,
        meta={"fastmcp": {"wrap_result": True}} if wrap_data else None,
    )


def for_fastmcp(
    factory_provider: Callable[..., Tool[Any]],
    *,
    spec: ToolSpec | None = None,
    max_chars: int | None = None,
) -> Callable[..., ToolResult] | Callable[..., Awaitable[ToolResult]]:
    """Build a wrapper function whose signature FastMCP can introspect.

    The tool class is inferred from *factory_provider*'s return type
    annotation.  The provider's unbound parameters with ``Depends``
    defaults are resolved by FastMCP at call time.

    Args:
        factory_provider: Callable that returns a Tool instance.
            Must have a return annotation that is a ``Tool`` subclass.
        spec: An already-derived contract, which :func:`register_mcp_tools`
            passes so it can register the matching output schema without
            deriving the contract twice.
        max_chars: The most text a return shows, unbounded for ``None``.

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    contract = spec or ToolSpec.from_factory(factory_provider)

    # Constant once the schema is derived, so the branch is settled here rather
    # than re-answered on every call.
    schema = output_schema(contract.data_type)
    wrap_data = bool(schema and schema.get("x-fastmcp-wrap-result"))

    # Append _tool_ as KEYWORD_ONLY with Depends default.
    tool_param = inspect.Parameter(
        "_tool_",
        inspect.Parameter.KEYWORD_ONLY,
        default=Depends(factory_provider),
        annotation=Any,
    )
    # A bare item is accepted where a list is declared, as a model often sends
    # one, while the published schema still asks for the list.
    params = [
        param.replace(annotation=accept_scalar(param.annotation))
        for param in contract.params
    ]
    new_sig = inspect.Signature(
        parameters=[*params, tool_param],
        return_annotation=ToolResult,
    )

    new_annotations: dict[str, Any] = {
        "_tool_": Any,
        **{param.name: param.annotation for param in params},
        "return": ToolResult,
    }

    # Surface a ToolRetry as a FastMCP ToolError so the message reaches the MCP
    # client instead of being masked as an internal error.
    if contract.is_async:

        async def wrapper(**kwargs: Any) -> ToolResult:
            with translate_tool_retry(ToolError):
                result = await kwargs.pop("_tool_")(**kwargs)
                return wrap_tool_output(
                    result, contract, wrap_data=wrap_data, max_chars=max_chars
                )
    else:

        def wrapper(**kwargs: Any) -> ToolResult:
            with translate_tool_retry(ToolError):
                result = kwargs.pop("_tool_")(**kwargs)
                return wrap_tool_output(
                    result, contract, wrap_data=wrap_data, max_chars=max_chars
                )

    contract.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_mcp_tools(
    app: FastMCP,
    factories: Sequence[Callable[..., Tool[Any]]],
    *,
    max_chars: int,
) -> None:
    """Register multiple Tool factories on a FastMCP app.

    Each factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Args:
        app: The FastMCP application.
        factories: Sequence of factory callables.
        max_chars: The most text one return shows.
    """
    for factory in factories:
        spec = ToolSpec.from_factory(factory)
        fn = for_fastmcp(factory, spec=spec, max_chars=max_chars)
        app.tool(
            fn,
            name=factory_tool_name(fn),
            description=fn.__doc__,
            output_schema=output_schema(spec.data_type),
        )
