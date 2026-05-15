"""External MCP server builders."""

import asyncio
import warnings
from collections.abc import AsyncIterator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
from mcp.client.streamable_http import streamable_http_client
from pydantic_ai.mcp import MCPServerStreamableHTTP

from ..security import create_safe_async_client, validate_optional_external_url
from ..types import McpServerConfig

__all__ = ["build_mcp_server", "validate_mcp_servers"]


class _SafeMCPServerStreamableHTTP(MCPServerStreamableHTTP):
    def __init__(
        self,
        url: str,
        *,
        auth: Any = None,
        headers: dict[str, str] | None = None,
        tool_prefix: str | None = None,
    ) -> None:
        super().__init__(url=url, headers=headers, tool_prefix=tool_prefix)
        self._auth = auth

    @asynccontextmanager
    async def client_streams(self) -> AsyncIterator[Any]:
        timeout = httpx.Timeout(self.timeout, read=self.read_timeout)
        async with AsyncExitStack() as stack:
            http_client = await stack.enter_async_context(
                create_safe_async_client(
                    timeout=timeout,
                    headers=self.headers,
                    auth=self._auth,
                )
            )
            read_stream, write_stream, *_ = await stack.enter_async_context(
                streamable_http_client(self.url, http_client=http_client)
            )
            yield read_stream, write_stream


async def validate_mcp_servers(servers: Iterable[McpServerConfig]) -> None:
    """Run the async SSRF check on each server URL before dereferencing.

    Pydantic validators only check scheme/host shape (no DNS) so model
    construction stays non-blocking; this is the request-boundary hook
    that actually resolves the host and rejects private targets.
    """
    await asyncio.gather(
        *(validate_optional_external_url(s.url, "MCP server url") for s in servers)
    )


def build_mcp_server(server_cfg: McpServerConfig) -> MCPServerStreamableHTTP:
    """Build an external MCP server from a user-provided config.

    Callers that hand the server to an agent should wrap the result with
    ``.defer_loading()`` so its tools are hidden from the model's initial
    context and discovered on demand via tool search. This keeps user-
    supplied MCP servers (which can expose dozens of endpoints) from
    bloating the prompt.
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
        server = _SafeMCPServerStreamableHTTP(
            url=server_cfg.url,
            auth=oauth_provider,
            tool_prefix=server_cfg.tool_prefix,
        )
    else:
        server = _SafeMCPServerStreamableHTTP(
            url=server_cfg.url,
            headers=server_cfg.headers or {},
            tool_prefix=server_cfg.tool_prefix,
        )

    return server
