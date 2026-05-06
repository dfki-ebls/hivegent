"""Routes that do not require authentication."""

from fastapi import APIRouter

__all__ = ["router"]

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    """Report backend readiness.

    FastAPI does not serve HTTP requests until the lifespan startup completes,
    so a successful response here implies the backend has finished initializing
    and is ready to handle authenticated traffic.
    """
    return {"status": "ok"}
