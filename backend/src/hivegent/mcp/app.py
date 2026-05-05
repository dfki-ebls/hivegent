"""FastMCP application assembly for Hivegent."""

from fastmcp import FastMCP
from fastmcp.server.auth import OIDCProxy

from ..config import settings

__all__ = ["mcp_app"]

mcp_auth: OIDCProxy | None = None

if not settings.auth.disabled:
    mcp_auth = OIDCProxy(
        config_url=f"{settings.auth.issuer}/.well-known/openid-configuration",
        client_id=settings.mcp.client_id,
        client_secret=settings.mcp.client_secret,
        base_url=settings.mcp.base_url,
    )

mcp_app = FastMCP("Hivegent", auth=mcp_auth)
