"""FastMCP application assembly for Hivegent."""

from fastmcp import FastMCP
from fastmcp.server.auth import (
    AuthProvider,
    JWTVerifier,
    OIDCProxy,
    RemoteAuthProvider,
)
from pydantic import AnyHttpUrl

from ..auth import build_discovery_url, fetch_oidc_configuration
from ..config import settings

__all__ = ["mcp_app"]

mcp_auth: AuthProvider | None = None

if settings.mcp.enable and settings.auth.enable:
    if settings.mcp.mode == "proxy":
        mcp_auth = OIDCProxy(
            config_url=build_discovery_url(settings.auth.issuer),
            client_id=settings.mcp.client_id,
            client_secret=settings.mcp.client_secret,
            base_url=settings.mcp.base_url,
        )
    elif settings.mcp.mode == "remote":
        oidc_config = fetch_oidc_configuration(settings.auth.issuer)
        if not oidc_config.jwks_uri:
            raise ValueError("OIDC discovery document missing jwks_uri")
        mcp_auth = RemoteAuthProvider(
            token_verifier=JWTVerifier(
                jwks_uri=str(oidc_config.jwks_uri),
                issuer=settings.auth.issuer,
                audience=settings.auth.audience,
            ),
            authorization_servers=[AnyHttpUrl(settings.auth.issuer)],
            base_url=settings.mcp.base_url,
        )

mcp_app = FastMCP("Hivegent", auth=mcp_auth)
