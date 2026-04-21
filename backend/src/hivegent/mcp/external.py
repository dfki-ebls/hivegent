"""External MCP server builders."""

import warnings
from typing import Any

import httpx
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.toolsets import AbstractToolset

from ..types import McpServerConfig

__all__ = ["build_mcp_server"]


def build_mcp_server(server_cfg: McpServerConfig) -> AbstractToolset[Any]:
    """Build an external MCP server toolset from a user-provided config.

    The resulting toolset is wrapped with ``defer_loading()`` so its tools
    are hidden from the model's initial context and discovered on demand via
    tool search. This keeps user-supplied MCP servers (which can expose
    dozens of endpoints) from bloating the prompt.
    """
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
        server = MCPServerStreamableHTTP(
            url=server_cfg.url,
            http_client=http_client,
            tool_prefix=server_cfg.tool_prefix,
        )
    else:
        server = MCPServerStreamableHTTP(
            url=server_cfg.url,
            headers=server_cfg.headers or {},
            tool_prefix=server_cfg.tool_prefix,
        )

    return server.defer_loading()
