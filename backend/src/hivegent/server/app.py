"""FastAPI application assembly for Hivegent."""

import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from starlette.responses import PlainTextResponse, Response

from ..config import settings
from ..consistency import check_and_fix_all_stores
from ..mcp import mcp_app
from ..observability import configure_observability
from .routes import api_router

__all__ = ["app", "create_app", "mcp_http_app"]

# LanceDB registers an os.register_at_fork() hook that warns on every
# fork(), including the safe fork+exec used by uvicorn's reloader and
# Python's subprocess module.  Filter it out to keep logs clean.
warnings.filterwarnings("ignore", message="lance is not fork-safe")

mcp_http_app = mcp_app.http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run startup consistency checks, then delegate to the MCP lifespan."""
    await check_and_fix_all_stores()
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


def create_app() -> FastAPI:
    """Create the configured FastAPI application."""
    app = FastAPI(lifespan=lifespan)
    configure_observability(app)
    app.add_middleware(
        CORSMiddleware,  # type: ignore[arg-type]
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.include_router(api_router)
    app.mount("/mcp", mcp_http_app)
    return app


app = create_app()
