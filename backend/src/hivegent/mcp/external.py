"""External MCP server builders."""

import asyncio
import warnings
from collections.abc import Iterable
from typing import Any

import httpx
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from ..config import settings
from ..security import create_legacy_safe_async_client, require_safe_external_url
from ..types import McpServerConfig

__all__ = ["build_mcp_server", "build_mcp_toolset", "validate_mcp_servers"]


def _safe_httpx_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Adapt :func:`create_safe_async_client` to FastMCP's client factory protocol.

    The explicit positional-or-keyword parameters match ``McpHttpClientFactory``;
    ``**kwargs`` absorbs transport-supplied extras such as ``follow_redirects``.
    """
    return create_legacy_safe_async_client(
        policy=settings.security.user_policy(),
        headers=headers,
        timeout=timeout,
        auth=auth,
        **kwargs,
    )


async def validate_mcp_servers(servers: Iterable[McpServerConfig]) -> None:
    """Run the async SSRF check on each server URL before dereferencing.

    Pydantic validators only check scheme/host shape (no DNS) so model
    construction stays non-blocking; this is the request-boundary hook
    that actually resolves the host and rejects private targets.
    """
    policy = settings.security.user_policy()
    await asyncio.gather(
        *(
            require_safe_external_url(s.url, "MCP server url", policy=policy)
            for s in servers
        )
    )


def build_mcp_toolset(server_cfg: McpServerConfig) -> MCPToolset[Any]:
    """Build an SSRF-safe MCP toolset from a user-provided config.

    Each connection is opened through :func:`create_legacy_safe_async_client`, which
    FastMCP closes after every session, so the factory mints a fresh client
    per connection. Routing all HTTP traffic (including the OAuth2 token
    exchange) through it rejects private and reserved hosts at connect time,
    even after DNS rebinding.
    """
    auth: httpx.Auth | None = None
    if server_cfg.oauth2:
        from fastmcp.client.auth.oauth import TokenStorageAdapter
        from key_value.aio.stores.memory import MemoryStore
        from mcp.client.auth.extensions.client_credentials import (
            ClientCredentialsOAuthProvider,
        )

        store = MemoryStore()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            storage = TokenStorageAdapter(store, server_url=server_cfg.url)
        auth = ClientCredentialsOAuthProvider(
            server_url=server_cfg.url,
            storage=storage,
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

    Callers that hand the toolset to an agent should wrap the result with
    ``.defer_loading()`` so its tools are hidden from the model's initial
    context and discovered on demand via tool search. This keeps user-
    supplied MCP servers (which can expose dozens of endpoints) from
    bloating the prompt.
    """
    toolset = build_mcp_toolset(server_cfg)
    if server_cfg.tool_prefix:
        return toolset.prefixed(server_cfg.tool_prefix)
    return toolset
