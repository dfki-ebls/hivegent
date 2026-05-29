"""Routes for tokens, memory, and user-wide cleanup."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ... import workspace
from ...auth import User, get_current_user
from ...db.memory import clear_memory
from ...db.tokens import create_token, list_tokens, revoke_all_tokens, revoke_token
from ...db.users import delete_user
from ...types import (
    BulkDeleteUserDataResponse,
    BulkRevokeTokensResponse,
    ClearMemoryResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    TokenInfo,
)
from ..common import user_store

__all__ = ["router"]

router = APIRouter()


@router.post("/tokens")
async def create_token_route(
    request: CreateTokenRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateTokenResponse:
    """Create a new personal access token."""
    created = await create_token(
        user_id=user.id,
        name=request.name,
        expires_in_days=request.expires_in_days,
    )
    return CreateTokenResponse(
        token=created.raw,
        id=created.info.id,
        name=created.info.name,
        created_at=created.info.created_at,
        expires_at=created.info.expires_at,
    )


@router.get("/tokens")
async def list_tokens_route(
    user: Annotated[User, Depends(get_current_user)],
) -> list[TokenInfo]:
    """List all personal access tokens for the current user."""
    return await list_tokens(user.id)


@router.delete("/tokens/{token_id}")
async def revoke_token_route(
    token_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke a personal access token."""
    if not await revoke_token(user.id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")


@router.delete("/tokens")
async def revoke_all_tokens_route(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkRevokeTokensResponse:
    """Revoke all personal access tokens for the authenticated user."""
    return BulkRevokeTokensResponse(
        revoked_count=await revoke_all_tokens(user.id),
        message="All tokens revoked successfully",
    )


@router.delete("/memory")
async def delete_memory(
    user: Annotated[User, Depends(get_current_user)],
) -> ClearMemoryResponse:
    """Clear the authenticated user's persistent memory."""
    cleared = await clear_memory(user.id)
    return ClearMemoryResponse(
        cleared=cleared,
        message="Memory cleared" if cleared else "No memory to clear",
    )


@router.delete("/user-data")
async def delete_all_user_data(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteUserDataResponse:
    """Delete all data for the authenticated user.

    ``workspace.delete_all`` clears documents (cascades to chunks via
    FK ``ON DELETE CASCADE``) and the workspace directory.
    ``delete_user`` then drops the ``User`` row so memory,
    conversations, and tokens cascade.  The user re-materialises lazily
    on the next request via :func:`ensure_user`.
    """
    store = user_store(user)
    await workspace.delete_all(store)
    await delete_user(user.id)
    return BulkDeleteUserDataResponse(
        message="All user data deleted successfully",
    )
