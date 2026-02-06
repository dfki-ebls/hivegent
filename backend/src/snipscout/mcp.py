"""FastMCP server with OIDCProxy auth and Pydantic AI toolset bridge."""

import inspect
import typing
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import OIDCProxy
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.dependencies import get_access_token
from pydantic_ai import FunctionToolset
from pydantic_ai.tools import Tool
from starlette.types import ASGIApp

from .agent import UserDeps, rag_toolset
from .auth import auth_settings
from .config import settings

__all__ = ["create_mcp_app"]


class _McpRunContext:
    """Minimal RunContext substitute for MCP tool execution.

    Satisfies the ``ctx.deps.user_id`` access pattern used by all RAG tools.
    If a future tool accesses other RunContext attributes, it will raise
    a clear AttributeError.
    """

    def __init__(self, user_id: str) -> None:
        self.deps = UserDeps(user_id=user_id)


def _get_mcp_user_id() -> str:
    """Extract user ID from the MCP auth token's ``sub`` claim."""
    if auth_settings.disabled:
        return "localhost"
    token = get_access_token()
    if token is None:
        raise RuntimeError("No authenticated user in MCP context")
    sub = token.claims.get("sub")
    if not sub:
        raise RuntimeError("Token missing 'sub' claim")
    return str(sub)


def _make_run_context() -> _McpRunContext:
    """Build a duck-typed RunContext from the current MCP auth token."""
    return _McpRunContext(_get_mcp_user_id())


def _create_mcp_wrapper(tool: Tool[Any]) -> Callable[..., Any]:
    """Create a FastMCP-compatible wrapper for a Pydantic AI tool.

    The wrapper removes the ``ctx`` parameter from the function signature
    and injects a duck-typed RunContext at call time.  ``__signature__`` is
    overridden so FastMCP's ``ParsedFunction`` generates the correct JSON
    schema without seeing the internal ``**kwargs``.
    """
    original_fn = tool.function
    sig = inspect.signature(original_fn)

    # Build signature without ctx
    new_params = [p for name, p in sig.parameters.items() if name != "ctx"]
    new_sig = sig.replace(parameters=new_params)

    # Copy type hints without ctx (for get_type_hints() resolution)
    try:
        hints = typing.get_type_hints(original_fn)
        new_hints = {k: v for k, v in hints.items() if k != "ctx"}
    except Exception:
        new_hints = {}

    # Create wrapper — **kwargs is invisible to FastMCP due to __signature__
    if inspect.iscoroutinefunction(original_fn):

        async def wrapper(**kwargs: Any) -> Any:
            ctx = _make_run_context()
            return await original_fn(ctx, **kwargs)
    else:

        def wrapper(**kwargs: Any) -> Any:
            ctx = _make_run_context()
            return original_fn(ctx, **kwargs)

    # Override function metadata so FastMCP generates correct schema
    wrapper.__name__ = tool.name
    wrapper.__qualname__ = tool.name
    wrapper.__doc__ = tool.description
    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = new_hints
    wrapper.__module__ = original_fn.__module__

    return wrapper


def _bridge_toolset_to_mcp(
    toolset: FunctionToolset[UserDeps],
    mcp: FastMCP,
) -> None:
    """Register all Pydantic AI FunctionToolset tools on a FastMCP server.

    Iterates ``toolset.tools`` and creates FastMCP-compatible wrappers.
    Tools that take ``RunContext`` get the ``ctx`` parameter replaced with
    an injected user context derived from the MCP auth token.
    """
    for _name, tool in toolset.tools.items():
        if tool.takes_ctx:
            fn = _create_mcp_wrapper(tool)
        else:
            fn = tool.function
        mcp.tool(name=tool.name, description=tool.description)(fn)


def create_mcp_app() -> ASGIApp:
    """Create the FastMCP ASGI app with auth and bridged tools."""
    auth: OIDCProxy | None = None
    middleware = []

    if not auth_settings.disabled:
        auth = OIDCProxy(
            config_url=f"{auth_settings.issuer}/.well-known/openid-configuration",
            client_id=settings.mcp.client_id,
            client_secret=settings.mcp.client_secret,
            base_url=settings.mcp.base_url,
        )
        middleware.append(RequireAuthMiddleware)

    mcp_server = FastMCP("SnipScout", auth=auth)
    _bridge_toolset_to_mcp(rag_toolset, mcp_server)

    return mcp_server.http_app(path="/", middleware=middleware)
