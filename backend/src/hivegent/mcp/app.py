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
from ..prompts import WORKSPACE_PATH_INSTRUCTIONS

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
                # FastMCP matches audience exactly, so the API's ``hivegent-*``
                # prefix list does not apply here.  The /mcp resource validates
                # its own client id; an empty id leaves the check disabled.
                audience=settings.mcp.client_id,
            ),
            authorization_servers=[AnyHttpUrl(settings.auth.issuer)],
            base_url=settings.mcp.base_url,
        )

# The path grammar every document argument speaks is stated once here, the way
# ``WORKSPACE_PATH_INSTRUCTIONS`` states it once for an agent run, rather than
# repeated in the description of each of the eight arguments that name a path.
mcp_app = FastMCP(
    "Hivegent", auth=mcp_auth, instructions=WORKSPACE_PATH_INSTRUCTIONS.strip()
)
