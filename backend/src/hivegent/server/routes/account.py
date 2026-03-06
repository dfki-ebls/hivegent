"""Routes for tokens, memory, and user-wide cleanup."""

import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ...auth import User, get_current_user
from ...config import settings
from ...memory import clear_memory
from ...retrieval import invalidate_store
from ...tokens import token_store
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
async def create_token(
    request: CreateTokenRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateTokenResponse:
    """Create a new personal access token."""
    created = token_store.create_token(
        user_id=user.id,
        name=request.name,
        expires_in_days=request.expires_in_days,
    )
    return CreateTokenResponse(
        token=created.raw_token,
        id=created.info.id,
        name=created.info.name,
        created_at=created.info.created_at,
        expires_at=created.info.expires_at,
    )


@router.get("/tokens")
async def list_tokens(
    user: Annotated[User, Depends(get_current_user)],
) -> list[TokenInfo]:
    """List all personal access tokens for the current user."""
    return token_store.list_tokens(user.id)


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke a personal access token."""
    if not token_store.revoke_token(user.id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")


@router.delete("/tokens")
async def revoke_all_tokens(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkRevokeTokensResponse:
    """Revoke all personal access tokens for the authenticated user."""
    tokens_path = user_store(user).tokens_path(settings.data_dir)
    count = len(token_store.list_tokens(user.id))
    if tokens_path.exists():
        tokens_path.unlink()
    return BulkRevokeTokensResponse(
        revoked_count=count,
        message="All tokens revoked successfully",
    )


@router.delete("/memory")
async def delete_memory(
    user: Annotated[User, Depends(get_current_user)],
) -> ClearMemoryResponse:
    """Clear the authenticated user's persistent memory."""
    cleared = clear_memory(user.id)
    return ClearMemoryResponse(
        cleared=cleared,
        message="Memory cleared" if cleared else "No memory to clear",
    )


@router.delete("/user-data")
async def delete_all_user_data(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteUserDataResponse:
    """Delete all data for the authenticated user."""
    store = user_store(user)
    invalidate_store(store)
    user_dir = store.root_dir(settings.data_dir)
    if user_dir.exists():
        shutil.rmtree(user_dir)
    return BulkDeleteUserDataResponse(
        message="All user data deleted successfully",
    )
