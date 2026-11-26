"""API router assembly for the server package."""

from fastapi import APIRouter, Depends

from ...auth import get_current_principal, get_current_user
from ...config import API_PREFIX, settings
from ..problems import PROBLEM_RESPONSES
from ..rate_limit import ADMISSION
from .account import router as account_router
from .admin import router as admin_router
from .cases import router as cases_router
from .conversations import chat_router
from .conversations import router as conversations_router
from .debug import router as debug_router
from .documents import router as documents_router
from .jobs import router as jobs_router
from .meta import router as meta_router
from .principal import router as principal_router
from .skills import router as skills_router
from .transcription import router as transcription_router
from .transparency import router as transparency_router

__all__ = ["api_router", "people_router"]

api_router = APIRouter(
    prefix=API_PREFIX,
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_current_principal), ADMISSION],
)
# Every route but running and following a turn serves people alone, so a service
# principal reaches those two and nothing a later router adds.
people_router = APIRouter(dependencies=[Depends(get_current_user)])
people_router.include_router(meta_router)
people_router.include_router(jobs_router)
people_router.include_router(conversations_router)
people_router.include_router(transcription_router)
people_router.include_router(transparency_router)
people_router.include_router(documents_router)
people_router.include_router(cases_router)
people_router.include_router(skills_router)
people_router.include_router(account_router)
people_router.include_router(admin_router)

if settings.debug.enable:
    people_router.include_router(debug_router)

api_router.include_router(people_router)
api_router.include_router(chat_router)
api_router.include_router(principal_router)
