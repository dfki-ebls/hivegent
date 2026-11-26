"""Routes for conversations and chat orchestration."""

from fastapi import APIRouter

from . import history, tasks, titles, turn

__all__ = ["chat_router", "router"]

router = APIRouter()
router.include_router(history.router)
router.include_router(tasks.router)
router.include_router(titles.router)
router.include_router(turn.router)

chat_router = turn.chat_router
