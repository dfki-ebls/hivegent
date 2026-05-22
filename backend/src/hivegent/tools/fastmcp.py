"""Adapter utilities for registering Tool classes with FastMCP."""

import base64
import inspect
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.tools import ToolResult
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
)

from .base import BinaryAttachment, CallInfo, Tool, ToolOutput, factory_tool_name

__all__ = ["for_fastmcp", "register_mcp_tools"]


def _attachment_to_block(att: BinaryAttachment) -> ContentBlock:
    """Map a framework-neutral attachment to an MCP content block."""
    encoded = base64.b64encode(att.data).decode("ascii")
    if att.media_type.startswith("image/"):
        return ImageContent(
            type="image",
            data=encoded,
            mimeType=att.media_type,
        )
    # Percent-encode the identifier so pydantic AnyUrl doesn't silently
    # collapse `..` segments or reinterpret `?`/`#`/space.
    uri = f"hivegent://attachment/{quote(att.identifier or 'blob', safe='')}"
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=uri,  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            mimeType=att.media_type,
            blob=encoded,
        ),
    )


def wrap_tool_output(result: ToolOutput[Any]) -> ToolResult:
    """Convert a :class:`ToolOutput` into an MCP :class:`ToolResult`.

    Text is always emitted as a :class:`TextContent` block; binary
    attachments are converted to :class:`ImageContent` for images or
    :class:`EmbeddedResource` for other media (e.g. PDFs) so MCP
    clients can render them.
    """
    blocks: list[ContentBlock] = [TextContent(type="text", text=result.text)]
    blocks.extend(_attachment_to_block(att) for att in result.attachments)
    return ToolResult(content=blocks)


def for_fastmcp(
    factory_provider: Callable[..., Tool[Any]],
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

        async def wrapper(**kwargs: Any) -> Any:
            return wrap_tool_output(await kwargs.pop("_tool_")(**kwargs))
    else:

        def wrapper(**kwargs: Any) -> Any:
            return wrap_tool_output(kwargs.pop("_tool_")(**kwargs))

    info.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_mcp_tools(
    app: FastMCP,
    factories: Sequence[Callable[..., Tool[Any]]],
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
