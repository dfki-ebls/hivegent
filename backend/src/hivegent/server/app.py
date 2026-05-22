"""FastAPI application assembly for Hivegent."""

import logging
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.responses import PlainTextResponse, Response

from ..config import settings
from ..db import init_database
from ..http_client import shared_http_client_lifespan
from ..mcp import mcp_app
from ..observability import configure_observability
from ..reconcile import reconcile_all
from .routes import api_router
from .routes.public import router as public_router

__all__ = ["app", "create_app", "mcp_http_app"]

logger = logging.getLogger(__name__)

# LanceDB registers an os.register_at_fork() hook that warns on every
# fork(), including the safe fork+exec used by uvicorn's reloader and
# Python's subprocess module.  Filter it out to keep logs clean.
warnings.filterwarnings("ignore", message="lance is not fork-safe")

mcp_http_app = mcp_app.http_app(path="/") if settings.mcp.enable else None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources and delegate to MCP."""
    async with shared_http_client_lifespan():
        await init_database()
        try:
            reports = await reconcile_all()
        except Exception:
            logger.warning("Startup reconciliation failed", exc_info=True)
        else:
            for key, report in reports.items():
                logger.info("Reconciled %s: %s", key, report)
        if mcp_http_app is None:
            yield
        else:
            async with mcp_http_app.lifespan(app):
                yield


async def validation_error_handler(
    _request: Request,
    exc: Exception,
) -> Response:
    """Return 422 for request validation errors."""
    if not isinstance(exc, ValidationError):
        raise exc
    return PlainTextResponse(str(exc), status_code=422)


def _validate_auth_settings() -> None:
    if settings.auth.enable:
        return
    if not settings.auth.allow_disabled:
        raise ValueError(
            "Authentication is disabled. Set HIVEGENT_AUTH__ALLOW_DISABLED=1 only "
            "for an isolated development environment."
        )
    if settings.mcp.enable and not settings.mcp.allow_unauthenticated:
        raise ValueError(
            "MCP must not be enabled while authentication is disabled unless "
            "HIVEGENT_MCP__ALLOW_UNAUTHENTICATED=1 is set for loopback-only "
            "development."
        )
    logger.warning(
        "AUTH DISABLED — every request is authenticated as the dev user "
        "'localhost' with write access to every group on disk. Never expose "
        "this server through a public listener or reverse proxy."
    )


def create_app() -> FastAPI:
    """Create the configured FastAPI application.

    Edge concerns — TLS, CORS, security headers, rate limiting, body-size
    caps, compression — live in the reverse proxy (Caddy). The backend
    keeps only domain logic, auth, and observability.
    """
    _validate_auth_settings()

    app = FastAPI(
        lifespan=lifespan,
        docs_url="/docs" if settings.security.expose_api_docs else None,
        redoc_url="/redoc" if settings.security.expose_api_docs else None,
        openapi_url="/openapi.json" if settings.security.expose_api_docs else None,
    )
    configure_observability(app)
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.include_router(public_router)
    app.include_router(api_router)
    if mcp_http_app is not None:
        app.mount("/mcp", mcp_http_app)
    return app


app = create_app()
