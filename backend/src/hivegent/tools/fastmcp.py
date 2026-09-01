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
    factory_tool_name,
    translate_tool_retry,
)

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
    result: ToolOutput[Any], spec: ToolSpec, *, wrap_data: bool
) -> ToolResult:
    """Convert a :class:`ToolOutput` into an MCP :class:`ToolResult`.

    Text is always emitted as a :class:`TextContent` block; binary
    attachments follow as image/audio/resource blocks.

    The structured payload is serialised through the spec's own adapter, so it
    matches the schema registered for the tool, and is wrapped exactly when
    that schema says it is.  Returning a built :class:`ToolResult` is what
    keeps the text and the attachments, which FastMCP would otherwise replace
    with a rendering of the structured content, so the wrapping it does for a
    raw return value is mirrored here rather than inherited.
    """
    blocks: list[ContentBlock] = [TextContent(type="text", text=result.text)]
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
    omit: Sequence[Any] = (),
    spec: ToolSpec | None = None,
) -> Callable[..., ToolResult] | Callable[..., Awaitable[ToolResult]]:
    """Build a wrapper function whose signature FastMCP can introspect.

    The tool class is inferred from *factory_provider*'s return type
    annotation.  The provider's unbound parameters with ``Depends``
    defaults are resolved by FastMCP at call time.

    Args:
        factory_provider: Callable that returns a Tool instance.
            Must have a return annotation that is a ``Tool`` subclass.
        omit: Annotations whose parameters this surface cannot honour, and
            so leaves out of the built signature.  The tool's own default
            stands in for each one at call time.
        spec: An already-derived contract, which :func:`register_mcp_tools`
            passes so it can register the matching output schema without
            deriving the contract twice.

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    contract = spec or ToolSpec.from_factory(factory_provider).without(*omit)

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
    new_sig = inspect.Signature(
        parameters=[*contract.params, tool_param],
        return_annotation=ToolResult,
    )

    new_annotations: dict[str, Any] = {
        "_tool_": Any,
        **contract.annotations,
        "return": ToolResult,
    }

    # Surface a ToolRetry as a FastMCP ToolError so the message reaches the MCP
    # client instead of being masked as an internal error.
    if contract.is_async:

        async def wrapper(**kwargs: Any) -> ToolResult:
            with translate_tool_retry(ToolError):
                result = await kwargs.pop("_tool_")(**kwargs)
                return wrap_tool_output(result, contract, wrap_data=wrap_data)
    else:

        def wrapper(**kwargs: Any) -> ToolResult:
            with translate_tool_retry(ToolError):
                result = kwargs.pop("_tool_")(**kwargs)
                return wrap_tool_output(result, contract, wrap_data=wrap_data)

    contract.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_mcp_tools(
    app: FastMCP,
    factories: Sequence[Callable[..., Tool[Any]]],
    *,
    omit: Sequence[Any] = (),
) -> None:
    """Register multiple Tool factories on a FastMCP app.

    Each factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Args:
        app: The FastMCP application.
        factories: Sequence of factory callables.
        omit: Annotations whose parameters to leave out of every signature.
    """
    for factory in factories:
        spec = ToolSpec.from_factory(factory).without(*omit)
        fn = for_fastmcp(factory, spec=spec)
        app.tool(
            fn,
            name=factory_tool_name(fn),
            description=fn.__doc__,
            output_schema=output_schema(spec.data_type),
        )
