"""Shared helpers for the MCP package."""

from typing import Annotated

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from pydantic import Field

from ..config import settings
from ..store import Casebase

__all__ = [
    "ExploreTaskArg",
    "get_mcp_group_stores",
    "get_mcp_user_id",
    "get_mcp_user_store",
]

ExploreTaskArg = Annotated[
    str,
    Field(description="Natural language description of what to explore or find."),
]


def get_mcp_user_id(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> str:
    """Extract the user ID from the MCP auth token."""
    if not settings.auth.enable:
        return "localhost"
    if access_token is None:
        raise RuntimeError("No authenticated user in MCP context")
    sub = access_token.claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise RuntimeError("Token missing 'sub' claim")
    return sub


def get_mcp_user_store(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> Casebase:
    """Build the user's casebase from the MCP auth token."""
    return Casebase.for_user(get_mcp_user_id(access_token))


def get_mcp_group_stores(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> tuple[Casebase, ...]:
    """Build group casebases from the MCP auth token."""
    if not settings.auth.enable:
        return ()
    if access_token is None:
        return ()
    raw = access_token.claims.get(settings.groups.groups_claim, [])
    if not isinstance(raw, list):
        return ()
    stores: list[Casebase] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        group_id = entry.rpartition(":")[0] if ":" in entry else entry
        if group_id in seen:
            continue
        seen.add(group_id)
        try:
            stores.append(Casebase.for_group(group_id))
        except ValueError:
            continue
    return tuple(stores)
