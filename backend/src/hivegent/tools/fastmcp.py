"""Adapter utilities for registering Tool classes with FastMCP."""

import base64
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any, cast, override
from urllib.parse import quote

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool, ToolResult
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
    ToolAnnotations,
)

from .base import (
    BinaryAttachment,
    Tool,
    ToolOutput,
    ToolSpec,
    lenient,
    translate_tool_retry,
)
from .formatting import truncate_middle

__all__ = [
    "OutputTool",
    "attachment_block",
    "for_fastmcp",
    "register_mcp_tool",
    "register_mcp_tools",
]


def attachment_block(att: BinaryAttachment) -> ContentBlock:
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


class OutputTool(FunctionTool):
    """A :class:`FunctionTool` whose function returns a :class:`ToolOutput`.

    The function declares its payload type as its return type, so FastMCP
    derives the output schema, and with it whether the payload is wrapped,
    the way it does for any tool.  :meth:`convert_result` then hands FastMCP
    only the payload and swaps in the text and attachments as the content,
    which FastMCP would otherwise render from the payload.
    """

    @override
    def convert_result(self, raw_value: Any) -> ToolResult:
        output = cast(ToolOutput[Any], raw_value)
        blocks: list[ContentBlock] = [TextContent(type="text", text=output.text)]
        blocks.extend(attachment_block(att) for att in output.attachments)

        return (
            super().convert_result(output.data).model_copy(update={"content": blocks})
        )


def for_fastmcp(
    factory_provider: Callable[..., Tool[Any]],
    *,
    max_chars: int | None = None,
) -> Callable[..., ToolOutput[Any]] | Callable[..., Awaitable[ToolOutput[Any]]]:
    """Build a wrapper function whose signature FastMCP can introspect.

    The tool class is inferred from *factory_provider*'s return type
    annotation.  The provider's unbound parameters with ``Depends``
    defaults are resolved by FastMCP at call time.  The wrapper is meant for
    :class:`OutputTool`, so its return annotation is the payload type while
    it returns the whole :class:`ToolOutput`.

    Args:
        factory_provider: Callable that returns a Tool instance.
            Must have a return annotation that is a ``Tool`` subclass.
        max_chars: The most text a return shows, unbounded for ``None``.
            An MCP client has no ``/tmp`` of this server's to read the rest
            from, so the structured content is what stays whole.

    Returns:
        A callable with rewritten signature, annotations, and docstring.
    """
    spec = ToolSpec.from_factory(factory_provider)

    tool_param = inspect.Parameter(
        "_tool_",
        inspect.Parameter.KEYWORD_ONLY,
        default=Depends(factory_provider),
        annotation=Any,
    )
    # Model-written arguments are repaired as an agent's are, while the
    # published schema stays the declared one.
    params = [
        param.replace(annotation=lenient(param.annotation)) for param in spec.params
    ]
    new_sig = inspect.Signature(
        parameters=[*params, tool_param],
        return_annotation=spec.data_type,
    )

    new_annotations: dict[str, Any] = {
        "_tool_": Any,
        **{param.name: param.annotation for param in params},
        "return": spec.data_type,
    }

    def bounded(output: ToolOutput[Any]) -> ToolOutput[Any]:
        if max_chars is None:
            return output

        return replace(output, formatted=truncate_middle(output.text, max_chars))

    # Surface a ToolRetry as a FastMCP ToolError so the message reaches the MCP
    # client instead of being masked as an internal error.
    if spec.is_async:

        async def wrapper(**kwargs: Any) -> ToolOutput[Any]:
            with translate_tool_retry(ToolError):
                return bounded(await kwargs.pop("_tool_")(**kwargs))
    else:

        def wrapper(**kwargs: Any) -> ToolOutput[Any]:
            with translate_tool_retry(ToolError):
                return bounded(kwargs.pop("_tool_")(**kwargs))

    spec.apply_to(wrapper, new_sig, new_annotations)
    return wrapper


def register_mcp_tool(
    app: FastMCP,
    factory: Callable[..., Tool[Any]],
    *,
    max_chars: int,
    annotations: ToolAnnotations | None = None,
) -> None:
    """Register one Tool factory on a FastMCP app.

    The factory's return type annotation must be a ``Tool`` subclass.
    The tool name and description are derived from the annotated class.

    Args:
        app: The FastMCP application.
        factory: Factory callable for the tool.
        max_chars: The most text one return shows.
        annotations: Optional hints for the client, such as whether the tool
            only reads or may destroy.
    """
    fn = for_fastmcp(factory, max_chars=max_chars)
    app.add_tool(OutputTool.from_function(fn, annotations=annotations))


def register_mcp_tools(
    app: FastMCP,
    factories: Sequence[Callable[..., Tool[Any]]],
    *,
    max_chars: int,
    annotations: ToolAnnotations | None = None,
) -> None:
    """Register multiple Tool factories on a FastMCP app with the same options.

    Args:
        app: The FastMCP application.
        factories: Sequence of factory callables.
        max_chars: The most text one return shows.
        annotations: Optional hints applied to every tool.
    """
    for factory in factories:
        register_mcp_tool(app, factory, max_chars=max_chars, annotations=annotations)
