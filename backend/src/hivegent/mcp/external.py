"""External MCP server builders."""

from collections.abc import Iterable
from typing import Any

import httpx2
from fastmcp.client.auth import ClientCredentialsOAuthProvider
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from ..config import settings
from ..security import create_safe_async_client, require_safe_external_url
from ..types import McpServerConfig

__all__ = ["build_mcp_server", "build_mcp_toolset", "validate_mcp_servers"]


def _safe_httpx_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    follow_redirects: bool = False,
) -> httpx2.AsyncClient:
    """Adapt :func:`create_safe_async_client` to FastMCP's client factory protocol.

    The first three parameters are ``McpHttpClientFactory``; ``follow_redirects``
    is the one extra the streamable-HTTP transport passes.  Naming every
    parameter keeps the untrusted client's proxy and transport out of reach of
    whatever a future FastMCP release decides to hand its factory.
    """
    return create_safe_async_client(
        policy=settings.security.user_policy(),
        proxy_url=settings.security.egress_proxy_url,
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=follow_redirects,
    )


def validate_mcp_servers(servers: Iterable[McpServerConfig]) -> None:
    """Apply the user URL allowlist before dereferencing MCP server URLs."""
    policy = settings.security.user_policy()
    for server in servers:
        require_safe_external_url(server.url, "MCP server url", policy=policy)


def build_mcp_toolset(server_cfg: McpServerConfig) -> MCPToolset[Any]:
    """Build an SSRF-safe MCP toolset from a user-provided config.

    Each connection is opened through :func:`create_safe_async_client`, which
    FastMCP closes after every session, so the factory mints a fresh client
    per connection.
    Routing all HTTP traffic, including the OAuth2 token exchange, through it
    applies the host policy on every request and leaves address validation to
    the egress proxy.
    """
    auth: httpx2.Auth | None = None
    if server_cfg.oauth2:
        auth = ClientCredentialsOAuthProvider(
            client_id=server_cfg.oauth2.client_id,
            client_secret=server_cfg.oauth2.client_secret,
            scopes=server_cfg.oauth2.scopes,
        )

    transport = StreamableHttpTransport(
        url=server_cfg.url,
        headers=server_cfg.headers or None,
        auth=auth,
        httpx_client_factory=_safe_httpx_client_factory,
    )
    return MCPToolset(transport)


def build_mcp_server(server_cfg: McpServerConfig) -> AbstractToolset[Any]:
    """Build an agent-ready toolset for an MCP server, applying its tool prefix.

    The result is deferred, so its tools are hidden from the model's initial
    context and discovered on demand via tool search.  A user-supplied server
    is the one part of the prompt this application does not size: it can expose
    dozens of endpoints, each with its own schema, on every request of every
    conversation that names it.  Deferring is applied here rather than left to
    the caller because there is no agent for which the eager alternative is
    right.
    """
    toolset = build_mcp_toolset(server_cfg)
    if server_cfg.tool_prefix:
        toolset = toolset.prefixed(server_cfg.tool_prefix)
    return toolset.defer_loading()
