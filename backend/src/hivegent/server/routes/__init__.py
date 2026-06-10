"""API router assembly for the server package."""

from fastapi import APIRouter, Depends

from ...auth import get_current_user
from .account import router as account_router
from .admin import router as admin_router
from .conversations import router as conversations_router
from .debug import router as debug_router
from .directories import router as directories_router
from .documents import router as documents_router
from .meta import router as meta_router
from .transcription import router as transcription_router

__all__ = ["api_router"]

api_router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])
api_router.include_router(meta_router)
api_router.include_router(conversations_router)
api_router.include_router(transcription_router)
api_router.include_router(documents_router)
api_router.include_router(directories_router)
api_router.include_router(account_router)
api_router.include_router(admin_router)
api_router.include_router(debug_router)
