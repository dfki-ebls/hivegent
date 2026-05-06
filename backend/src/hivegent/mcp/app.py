"""FastMCP application assembly for Hivegent."""

from fastmcp import FastMCP
from fastmcp.server.auth import (
    AuthProvider,
    JWTVerifier,
    OIDCProxy,
    RemoteAuthProvider,
)
from pydantic import AnyHttpUrl

from ..config import settings

__all__ = ["mcp_app"]

mcp_auth: AuthProvider | None = None

if not settings.auth.disabled:
    if settings.mcp.mode == "proxy":
        mcp_auth = OIDCProxy(
            config_url=f"{settings.auth.issuer}/.well-known/openid-configuration",
            client_id=settings.mcp.client_id,
            client_secret=settings.mcp.client_secret,
            base_url=settings.mcp.base_url,
        )
    elif settings.mcp.mode == "remote":
        mcp_auth = RemoteAuthProvider(
            token_verifier=JWTVerifier(
                jwks_uri=f"{settings.auth.issuer}/.well-known/jwks.json",
                issuer=settings.auth.issuer,
                audience=settings.auth.audience,
            ),
            authorization_servers=[AnyHttpUrl(settings.auth.issuer)],
            base_url=settings.mcp.base_url,
        )

mcp_app = FastMCP("Hivegent", auth=mcp_auth)
