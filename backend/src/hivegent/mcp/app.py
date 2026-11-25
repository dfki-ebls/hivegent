"""FastMCP application assembly for Hivegent."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import override

from exar.mcp import Confirmations, server
from fastapi import HTTPException
from fastmcp.server.auth import (
    AccessToken,
    AuthProvider,
    OIDCProxy,
    RemoteAuthProvider,
    TokenVerifier,
)
from fastmcp.server.http import StarletteWithLifespan
from pydantic import AnyHttpUrl
from starlette.middleware import Middleware
from starlette.routing import BaseRoute, Mount, Route

from ..auth import authenticate, build_discovery_url, dev_user
from ..config import settings
from ..config.common import MCP_PATH
from ..db.confirmations import PostgresNonces
from ..prompts import WORKSPACE_PATH_INSTRUCTIONS
from ..shared.l10n import DEFAULT_LANGUAGE
from ..store import User
from .adapter import POLICY, agent_tools, token_user

__all__ = ["HivegentVerifier", "McpApp", "build_mcp_app"]


class HivegentVerifier(TokenVerifier):
    """Verifies the issuer's tokens for *audience* with :func:`~hivegent.auth.authenticate`.

    The verification the REST API runs, so ``/mcp`` accepts the same tokens
    but for their audience, and logs why it refused one.
    """

    def __init__(self, audience: Collection[str]) -> None:
        super().__init__()
        self.audience = audience

    @override
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            credential = await authenticate(token, self.audience)
        except HTTPException:
            return None

        claims = credential.claims
        scope = claims.get("scope")

        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or ""),
            scopes=scope.split() if isinstance(scope, str) else [],
            expires_at=int(credential.expires_at),
            subject=credential.user.id,
            claims=dict(claims),
        )


def _auth() -> AuthProvider:
    """The issuer's authentication in the configured mode."""
    issuer = settings.auth.issuer
    base_url = settings.mcp.base_url

    if issuer is None or base_url is None:
        raise ValueError("/mcp needs auth.issuer and mcp.base_url")

    # The resource is the origin joined with the endpoint's path, exactly
    # `base_url`, rather than the trailing-slash URL of a mount at `/mcp`.
    # Its settings hold its path to `MCP_PATH`, so stripping it leaves the origin.
    resource = str(base_url)
    origin = resource.removesuffix(MCP_PATH)
    client_id = settings.mcp.client_id

    if settings.mcp.mode == "proxy":
        if client_id is None or settings.mcp.client_secret is None:
            raise ValueError(
                "MCP proxy mode needs HIVEGENT_MCP__CLIENT_ID and "
                "HIVEGENT_MCP__CLIENT_SECRET, the client the proxy registered with "
                "the issuer."
            )

        # The upstream tokens are minted for the proxy's own client, FastMCP
        # then hands its clients tokens of its own.  Its OAuth routes and
        # issuer live below `/mcp`.
        return OIDCProxy(
            config_url=build_discovery_url(issuer),
            client_id=client_id,
            client_secret=settings.mcp.client_secret.get_secret_value(),
            base_url=resource,
            resource_base_url=origin,
            token_verifier=HivegentVerifier([client_id]),
        )

    # Dynamically registered clients each have their own id, so the token
    # names this resource (RFC 8707), or the configured client id.
    return RemoteAuthProvider(
        token_verifier=HivegentVerifier(
            [resource] if client_id is None else [resource, client_id]
        ),
        authorization_servers=[AnyHttpUrl(issuer)],
        base_url=resource,
        resource_base_url=origin,
    )


async def _dev_user(_token: AccessToken | None) -> User:
    return await dev_user()


@dataclass(frozen=True, slots=True)
class McpApp:
    """The ASGI app of the MCP server, whose lifespan the host runs, and the routes serving it at the host's root."""

    app: StarletteWithLifespan
    routes: list[BaseRoute]


def build_mcp_app(
    *, dev: bool = False, middleware: Sequence[Middleware] = ()
) -> McpApp:
    """The MCP server, called by the issuer's tokens, or as the development user under *dev*.

    The endpoint is served at :data:`MCP_PATH` and the discovery documents
    of its authentication where RFC 9728 and RFC 8414 place them, at the
    root, and the proxy's OAuth routes below ``/mcp``, where its ``base_url``
    advertises them.  *middleware* wraps the endpoint.

    The path grammar every document argument speaks is stated once here, the
    way ``WORKSPACE_PATH_INSTRUCTIONS`` states it once for an agent run, rather
    than repeated in the description of every argument that names a path.
    An MCP client has no interface language, so it is served the default one.
    It keeps no sessions, each request getting a transport of its own, since a
    confirmation travels with the retried call and nothing else asks the client.
    """
    auth = None if dev else _auth()
    key = settings.mcp.confirmation_key

    if key is None:
        raise ValueError("/mcp needs mcp.confirmation_key")

    confirmations = Confirmations(
        key=key.get_secret_value().encode(), nonces=PostgresNonces()
    )

    mcp = server(
        agent_tools(_dev_user if dev else token_user, confirmations),
        policy=POLICY,
        confirmations=confirmations,
        name="Hivegent",
        auth=auth,
        instructions=WORKSPACE_PATH_INSTRUCTIONS[DEFAULT_LANGUAGE].strip(),
    )
    app = mcp.http_app(path=MCP_PATH, middleware=list(middleware), stateless_http=True)
    discovery = [] if auth is None else auth.get_well_known_routes(MCP_PATH)
    proxied = [Mount(MCP_PATH, app)] if isinstance(auth, OIDCProxy) else []

    return McpApp(app, [*discovery, Route(MCP_PATH, app), *proxied])
