"""FastAPI application assembly for Hivegent."""

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.responses import PlainTextResponse, Response

from ..config import settings
from ..db import apply_migrations
from ..http_client import shared_http_client_lifespan
from ..mcp import mcp_app
from ..observability import configure_observability
from ..reconcile import reconcile_all
from ..retrieval import reconcile_index_state
from .routes import api_router
from .routes.public import router as public_router

__all__ = ["create_app", "mcp_http_app"]

logger = logging.getLogger(__name__)

mcp_http_app = mcp_app.http_app(path="/") if settings.mcp.enable else None


async def _verify_vector_dim() -> None:
    """Fail loudly when the live ``chunks.embedding`` dim differs from settings.

    pgvector stores the dimension in the column's ``atttypmod``.  We
    compare it to ``settings.embedding.dimension`` at boot so an
    operator who flipped the embedding model without generating a
    follow-up migration sees the error here, not mid-request.
    """
    from ..db.engine import get_engine

    expected = settings.embedding.dimension
    async with get_engine().connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )
        actual = result.scalar_one_or_none()
    if actual is None:
        raise RuntimeError("chunks.embedding column not found — did migrations run?")
    if actual != expected:
        raise RuntimeError(
            f"Embedding dim mismatch: chunks.embedding is {actual}, "
            f"settings.embedding resolves to {expected}.  Generate an Alembic "
            "revision against the new model before booting."
        )


async def _verify_fts_config() -> None:
    """Fail loudly when the live ``chunks.tsv`` configs differ from settings.

    ``alembic check`` does not compare generated-column expressions, so a
    changed ``settings.embedding.text_search_config`` without a follow-up
    migration would silently desync the stored ``tsvector`` from the
    query-side ``tsquery``.  We read the live generated expression and
    compare the ordered ``to_tsvector`` configurations to the configured
    ones at boot, mirroring :func:`_verify_vector_dim`.
    """
    from ..db.engine import get_engine

    cfg = settings.embedding.text_search_config
    expected = (cfg,) if isinstance(cfg, str) else tuple(cfg)
    async with get_engine().connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_expr(d.adbin, d.adrelid) "
                "FROM pg_attrdef d JOIN pg_attribute a "
                "ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                "WHERE d.adrelid = 'chunks'::regclass AND a.attname = 'tsv'"
            )
        )
        live_expr = result.scalar_one_or_none()
    if live_expr is None:
        raise RuntimeError(
            "chunks.tsv generated column not found — did migrations run?"
        )
    actual = tuple(re.findall(r"to_tsvector\(\s*'([^']+)'", live_expr))
    if actual != expected:
        raise RuntimeError(
            f"FTS config mismatch: chunks.tsv stems with {actual}, "
            f"settings.embedding.text_search_config resolves to {expected}.  "
            "Generate an Alembic revision against the new model before booting."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources and delegate to MCP."""
    async with shared_http_client_lifespan():
        await apply_migrations()
        await _verify_vector_dim()
        await _verify_fts_config()
        await reconcile_index_state()
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
    if settings.auth.enable and not settings.auth.issuer:
        raise ValueError(
            "Authentication is enabled but HIVEGENT_AUTH__ISSUER is unset."
        )
    if settings.auth.enable and not settings.auth.audience:
        raise ValueError(
            "Authentication is enabled but HIVEGENT_AUTH__AUDIENCE is empty. "
            "Set it to the client IDs allowed to call the API (token audiences)."
        )
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
