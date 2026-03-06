"""External MCP server builders."""

import warnings

import httpx
from pydantic_ai.mcp import MCPServerStreamableHTTP

from ..types import McpServerConfig

__all__ = ["build_mcp_server"]


def build_mcp_server(server_cfg: McpServerConfig) -> MCPServerStreamableHTTP:
    """Build an external MCP server toolset from a user-provided config."""
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
        oauth_provider = ClientCredentialsOAuthProvider(
            server_url=server_cfg.url,
            storage=storage,
            client_id=server_cfg.oauth2.client_id,
            client_secret=server_cfg.oauth2.client_secret,
            scopes=server_cfg.oauth2.scopes,
        )
        http_client = httpx.AsyncClient(auth=oauth_provider)
        return MCPServerStreamableHTTP(
            url=server_cfg.url,
            http_client=http_client,
            tool_prefix=server_cfg.tool_prefix,
        )

    return MCPServerStreamableHTTP(
        url=server_cfg.url,
        headers=server_cfg.headers or {},
        tool_prefix=server_cfg.tool_prefix,
    )
