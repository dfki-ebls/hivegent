"""Routes that do not require authentication."""

from fastapi import APIRouter

from ...config import settings
from ...types import FrontendConfigResponse, OidcPublicConfig

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


@router.get("/config")
async def config() -> FrontendConfigResponse:
    """Serve the browser SPA's runtime configuration.

    The single source of truth for the frontend's OIDC client: the issuer and
    client id are derived from the backend settings, so the SPA reads them here
    at startup instead of baking them in at build time. Unauthenticated (the SPA
    fetches it before login) and carries only public values.
    """
    return FrontendConfigResponse(
        oidc=OidcPublicConfig(
            issuer_uri=settings.auth.issuer,
            client_id=settings.auth.frontend_client_id,
        )
    )
