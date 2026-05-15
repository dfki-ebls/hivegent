"""FastAPI application assembly for Hivegent."""

import asyncio
import logging
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import settings
from ..consistency import check_and_fix_all_stores, run_periodic_consistency
from ..http_client import shared_http_client_lifespan
from ..mcp import mcp_app
from ..observability import configure_observability
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
    """Open shared resources, run startup consistency, delegate to MCP."""
    async with shared_http_client_lifespan():
        await check_and_fix_all_stores()
        tick_task: asyncio.Task[None] | None = None
        if settings.consistency_tick_interval_seconds > 0:
            tick_task = asyncio.create_task(
                run_periodic_consistency(settings.consistency_tick_interval_seconds),
                name="hivegent-consistency-tick",
            )
        try:
            if mcp_http_app is None:
                yield
            else:
                async with mcp_http_app.lifespan(app):
                    yield
        finally:
            if tick_task is not None:
                tick_task.cancel()
                try:
                    await tick_task
                except asyncio.CancelledError:
                    pass


async def validation_error_handler(
    _request: Request,
    exc: Exception,
) -> Response:
    """Return 422 for request validation errors."""
    if not isinstance(exc, ValidationError):
        raise exc
    return PlainTextResponse(str(exc), status_code=422)


# The API serves JSON and binary assets; no HTML the browser should ever
# render. A locked-down CSP makes any direct browser navigation to an API
# response inert.
_API_CSP = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": _API_CSP,
}


class SecurityHeadersMiddleware:
    """Attach defensive headers to every HTTP response.

    Pure ASGI implementation so SSE / streaming endpoints aren't buffered
    by ``BaseHTTPMiddleware``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self._app(scope, receive, send_with_headers)


def _validate_cors_origins(origins: list[str]) -> list[str]:
    """Reject the unsafe wildcard combo at startup.

    ``allow_origins=["*"]`` with ``allow_credentials=True`` is silently
    coerced by Starlette into a non-credential policy; fail loudly here
    so the misconfig surfaces immediately.
    """
    if "*" in origins:
        raise ValueError(
            "cors_origins must not include '*' — list explicit origins instead."
        )
    return origins


def create_app() -> FastAPI:
    """Create the configured FastAPI application."""
    if not settings.auth.enable:
        logger.warning(
            "AUTH DISABLED — every request is authenticated as the dev user "
            "'localhost' with write access to every group on disk. Never "
            "enable this in production or expose the server to a non-loopback "
            "interface."
        )
    cors_origins = _validate_cors_origins(settings.cors_origins)

    app = FastAPI(lifespan=lifespan)
    configure_observability(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,  # type: ignore[arg-type]
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.include_router(public_router)
    app.include_router(api_router)
    if mcp_http_app is not None:
        app.mount("/mcp", mcp_http_app)
    return app


app = create_app()
