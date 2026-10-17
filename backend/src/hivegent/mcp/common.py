"""Shared helpers for the MCP package."""

from typing import Annotated

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from pydantic import Field

from ..auth import dev_user, user_from_claims
from ..config import settings
from ..store import User

__all__ = ["ExploreTaskArg", "get_mcp_user"]

ExploreTaskArg = Annotated[
    str,
    Field(description="Natural language description of what to explore or find."),
]


async def get_mcp_user(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> User:
    """Build the caller's :class:`User` from the MCP auth token.

    The same construction the HTTP API uses, so an MCP client is granted
    exactly the groups and permissions its token grants there.
    """
    if not settings.auth.enable:
        return await dev_user()

    if access_token is None:
        raise RuntimeError("No authenticated user in MCP context")

    return user_from_claims(access_token.claims)
