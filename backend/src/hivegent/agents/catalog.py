"""The operator's external MCP servers, each connected once for the whole process.

Every entry of ``settings.mcp_servers`` is a :class:`CatalogServer` whose
:class:`~exar.mcp_client.McpClient` keeps one connection entered while the app
runs, reconnecting with a growing backoff after a failure.  A run never
connects itself: it is offered the tools the server last listed, and a call
while the connection is down fails at once with a short notice, so an outage
changes neither the tools nor the instructions of a run, which keeps its
cached prefix, and fails no turn.  This module adds what is Hivegent's, the settings,
the headers that authenticate the deployment, and the caller a server is told.

Every tool is named ``<id>_<name>``, and since an id has no underscore and none
prefixes a built-in tool (:func:`~.capabilities.check_tool_settings`), no two
tools of a run share a name.  Each declares the effect the host derives its
offering and approval from (`exar.mcp.annotated_effect`), and its output
passes the guards every tool's does.  A server whose ``groups`` the user is in
no member of is invisible to them, and one with no ``groups`` is the global
scope's, the only one a service principal sees.
"""

from collections.abc import AsyncGenerator, Callable, Collection, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from typing import Self, override

import httpx2
from exar.mcp_client import McpClient
from fastmcp.client.auth import ClientCredentialsOAuthProvider
from fastmcp.client.transports import StdioTransport
from fastmcp.mcp_config import infer_transport_type_from_url
from mcp.client.auth.utils import issuers_match
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPAuthFunc, MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from ..config import settings
from ..config.integrations import HttpMcpServer, McpServer, StdioMcpServer
from ..http_client import create_trusted_http_client
from ..security import Origin, origin
from ..shared.l10n import Localized
from ..store import User
from ..types import McpIntegration
from .common import UserDeps

__all__ = ["CATALOG", "USER_HEADER", "CatalogServer", "caller_id"]

USER_HEADER = "X-Hivegent-User"
"""The header naming who calls a server set to ``forward_user``, and nothing else of them."""


def caller_id(user: User) -> str:
    """Who *user* is to a server told who calls: the token's subject, a service its client id.

    >>> caller_id(User("alice"))
    'alice'
    """
    client = settings.auth.service_client_id

    return client if user.is_service and client is not None else user.id


def _unavailable(server_id: str, ctx: RunContext[UserDeps]) -> str:
    return Localized(
        en=f"The tools of the external server {server_id} are unavailable right now.",
        de=f"Die Tools des externen Servers {server_id} sind gerade nicht verfügbar.",
    )[ctx.deps.language]


def _endpoint_origins(
    oauth: ClientCredentialsOAuthProvider, issuer: str
) -> frozenset[Origin]:
    """The origins of the endpoints the discovery metadata of *issuer* names, once fetched."""
    metadata = oauth.context.oauth_metadata

    if metadata is None or not issuers_match(str(metadata.issuer), issuer):
        return frozenset()

    endpoints = (
        metadata.token_endpoint,
        metadata.registration_endpoint,
        metadata.revocation_endpoint,
        metadata.introspection_endpoint,
    )

    return frozenset(origin(str(url)) for url in endpoints if url is not None)


@dataclass(frozen=True, slots=True)
class _ServerAuth(httpx2.Auth):
    """Adds *headers* to the requests to the server at *server*, then authenticates as *oauth* does.

    The headers never reach the issuer *oauth* asks for a token.
    """

    requires_response_body = True

    server: Origin
    headers: Mapping[str, str]
    oauth: httpx2.Auth | None

    @override
    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if origin(request.url) == self.server:
            request.headers.update(self.headers)

        if self.oauth is None:
            yield request

            return

        flow = self.oauth.async_auth_flow(request)
        request = await anext(flow)

        while True:
            response = yield request

            try:
                request = await flow.asend(response)
            except StopAsyncIteration:
                return


def _stdio_toolset(server_id: str, config: StdioMcpServer) -> MCPToolset[UserDeps]:
    # Not kept alive, so leaving the toolset ends the process and entering it
    # again starts a fresh one.
    transport = StdioTransport(
        config.command,
        config.args,
        config.process_env,
        None if config.cwd is None else str(config.cwd),
        keep_alive=False,
    )

    return MCPToolset(
        transport,
        id=server_id,
        init_timeout=config.timeout_seconds,
        read_timeout=config.timeout_seconds,
        include_instructions=True,
    )


def _http_toolsets(
    server_id: str, config: HttpMcpServer
) -> tuple[MCPToolset[UserDeps], MCPToolset[UserDeps] | None]:
    """The toolset of a server over Streamable HTTP, and the one a call connects through when it is told who calls.

    Raises:
        ValueError: For an SSE endpoint, since only Streamable HTTP is spoken.
    """
    url = config.url

    if infer_transport_type_from_url(url) != "http":
        raise ValueError(
            f"mcp_servers.{server_id}.url names an SSE endpoint, "
            "only Streamable HTTP is supported"
        )

    oauth = config.oauth
    headers = config.request_headers
    oauth_auth: ClientCredentialsOAuthProvider | None = None
    discovered: Callable[[], Collection[Origin]] = frozenset

    if oauth is not None:
        oauth_auth = ClientCredentialsOAuthProvider(
            url,
            client_id=oauth.client_id,
            client_secret=oauth.client_secret.get_secret_value(),
            scopes=oauth.scopes or None,
            issuer=oauth.issuer,
        )
        discovered = partial(_endpoint_origins, oauth_auth, oauth.issuer)

    # An operator endpoint, so the direct client confined to it, its issuer,
    # and the endpoints the issuer's metadata names, which carries the
    # headers to the server alone.
    client = create_trusted_http_client(
        url,
        *(() if oauth is None else (oauth.issuer,)),
        timeout=httpx2.Timeout(config.timeout_seconds, read=None),
        discovered=discovered,
    )

    def toolset(auth: _ServerAuth | MCPAuthFunc[UserDeps]) -> MCPToolset[UserDeps]:
        return MCPToolset(
            url,
            id=server_id,
            http_client=client,
            auth=auth,
            init_timeout=config.timeout_seconds,
            read_timeout=config.timeout_seconds,
            include_instructions=True,
        )

    def forwarded(ctx: RunContext[UserDeps]) -> _ServerAuth:
        return _ServerAuth(
            origin(url),
            {**headers, USER_HEADER: caller_id(ctx.deps.user)},
            oauth_auth,
        )

    return (
        toolset(_ServerAuth(origin(url), headers, oauth_auth)),
        toolset(forwarded) if config.forward_user else None,
    )


def _client(
    server_id: str,
    config: McpServer,
    toolset: MCPToolset[UserDeps],
    caller: MCPToolset[UserDeps] | None = None,
) -> McpClient[UserDeps]:
    """The connection to the server *config* describes, through *toolset*."""
    return McpClient(
        name=server_id,
        toolset=toolset,
        allowed=config.allowed_tools,
        blocked=config.blocked_tools,
        effects=config.effects,
        trusted=config.trusted,
        refresh_seconds=config.refresh_seconds,
        caller=caller,
        unavailable=partial(_unavailable, server_id),
    )


@dataclass(frozen=True, slots=True)
class CatalogServer:
    """One configured server and its connection, offered as an integration."""

    id: str
    config: McpServer
    client: McpClient[UserDeps]

    @classmethod
    def build(cls, server_id: str, config: McpServer) -> Self:
        """The server *config* describes, not yet connected.

        Raises:
            ValueError: For an SSE endpoint, since only Streamable HTTP is spoken.
        """
        if isinstance(config, StdioMcpServer):
            return cls(
                server_id,
                config,
                _client(server_id, config, _stdio_toolset(server_id, config)),
            )

        toolset, caller = _http_toolsets(server_id, config)

        return cls(server_id, config, _client(server_id, config, toolset, caller))

    @property
    def groups(self) -> list[str]:
        return self.config.groups

    @property
    def toolset(self) -> AbstractToolset[UserDeps]:
        """The server's tools as a run offers them, prefixed by its id."""
        return self.client.agent_toolset()

    @property
    def instructions(self) -> None:
        """None, since a trusted server's instructions reach the run through its :attr:`toolset`."""
        return None

    def integration(self) -> McpIntegration:
        """The server as the admin listing shows it, without any secret."""
        return McpIntegration(
            id=self.id,
            host=self.config.host,
            groups=self.config.groups,
            trusted=self.config.trusted,
            connected=self.client.connected,
            error=self.client.error,
            tools={
                name: self.client.effect_of(tool)
                for name, tool in (self.client.tools or {}).items()
            },
        )

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        """Connect in the background, never awaited, so a slow or unreachable server delays nothing."""
        return self.client.supervised()


CATALOG: tuple[CatalogServer, ...] = tuple(
    CatalogServer.build(server_id, config)
    for server_id, config in settings.mcp_servers.items()
)
"""Every configured server."""
