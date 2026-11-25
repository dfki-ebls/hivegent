"""Routes that do not require authentication."""

from fastapi import APIRouter

from ...config import API_PREFIX, settings
from ...transparency import public_jwks
from ...types import FrontendConfigResponse, OidcPublicConfig, TransparencyJwks
from ..problems import PROBLEM_RESPONSES
from .transparency import TransparencyConfigured

__all__ = ["router"]

router = APIRouter(prefix=API_PREFIX, responses=PROBLEM_RESPONSES)


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

    The single source of truth for the frontend's OIDC client and handbook
    link: both are derived from the backend settings, so the SPA reads them
    here at startup instead of baking them in at build time.  No OIDC client
    means ``hivegent dev`` runs it as the development user, which the SPA
    mocks.  Unauthenticated (the SPA fetches it before login) and carries only
    public values.
    """
    issuer = settings.auth.issuer

    return FrontendConfigResponse(
        oidc=None
        if issuer is None
        else OidcPublicConfig(
            issuer_uri=issuer, client_id=settings.auth.frontend_client_id
        ),
        docs_url=settings.docs_url,
    )


@router.get("/transparency/jwks")
async def transparency_jwks(config: TransparencyConfigured) -> TransparencyJwks:
    """Publish the key that verifies detection reports and export provenance.

    Unauthenticated, since a report or an export handed on to someone without
    a sign-in is only verifiable while its key is reachable.
    """
    return public_jwks(config)
