"""FastAPI application assembly for Hivegent."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress

import sqlalchemy as sa
from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.responses import PlainTextResponse, Response

from ..agents import check_tool_settings
from ..config import settings
from ..db import apply_migrations, engine_lifespan
from ..http_client import shared_http_client_lifespan
from ..l10n import Localized
from ..logging_config import configure_logging
from ..mcp import mcp_app
from ..observability import configure_observability
from ..reconcile import reconcile_all
from ..retrieval import reconcile_index_state, warm_index
from ..sandbox import monty_pool_lifespan
from ..staging import prune_staged
from ..tmp import sweep_tmp
from ..workers.pool import pipeline_pool
from .access_log import install_probe_access_filter
from .language import LanguageMiddleware
from .maintenance import load_persisted_state
from .operations import cleanup_spool_dir
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


_HOUSEKEEPING: tuple[tuple[str, Callable[[], Awaitable[object]]], ...] = (
    ("staged changesets", prune_staged),
    ("/tmp folders", sweep_tmp),
)


async def _housekeep(interval: float) -> None:
    """Prune what expired now, then once every *interval* seconds."""
    while True:
        for name, job in _HOUSEKEEPING:
            try:
                _ = await job()
            except Exception:
                logger.warning("Cleaning up %s failed", name, exc_info=True)

        await asyncio.sleep(interval)


@asynccontextmanager
async def _housekeeping() -> AsyncGenerator[None]:
    """Run the cleanups in the background for as long as the app does.

    Never awaited by the boot: the first pass walks every ``/tmp`` folder,
    which the requests the app starts serving meanwhile do not wait on.
    """
    task = asyncio.create_task(_housekeep(settings.tmp.sweep_interval_hours * 3600))

    try:
        yield
    finally:
        _ = task.cancel()

        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open shared resources and delegate to MCP."""
    # Before anything is opened: a misspelled exclusion withholds nothing, and
    # the boot that would have surfaced it is the cheapest place to say so.
    check_tool_settings()

    async with (
        monty_pool_lifespan(),
        shared_http_client_lifespan(),
        engine_lifespan(),
        AsyncExitStack() as background,
    ):
        await apply_migrations()
        # After the migrations, since the /tmp sweep reads conversation rows.
        await background.enter_async_context(_housekeeping())
        await _verify_vector_dim()
        await _verify_fts_config()
        cleanup_spool_dir()
        await load_persisted_state(app)
        await reconcile_index_state()
        # Load the embedding model now so the first upload after boot does not
        # pay the sentence-transformers cold-start latency inline.
        await warm_index()
        try:
            reports = await reconcile_all()
        except Exception:
            logger.warning("Startup reconciliation failed", exc_info=True)
        else:
            for key, report in reports.items():
                logger.info("Reconciled %s: %s", key, report)
        try:
            if mcp_http_app is None:
                yield
            else:
                async with mcp_http_app.lifespan(app):
                    yield
        finally:
            # Tear down the persistent converter/chunker worker processes (a
            # no-op if the pool was never activated or used).
            await pipeline_pool.aclose()


def _invalid_data(title: str) -> Localized[str]:
    return Localized(en=f"Invalid data for {title}", de=f"Ungültige Daten für {title}")


async def validation_error_handler(
    _request: Request,
    exc: Exception,
) -> Response:
    """Return 422 for request validation errors."""
    if not isinstance(exc, ValidationError):
        raise exc

    # A validator's own ValueError is already localized, unlike pydantic's
    # messages, so it is shown without pydantic's English "Value error" prefix.
    errors = "\n".join(
        f"{'.'.join(str(part) for part in error['loc'])}: "
        + str(error.get("ctx", {}).get("error", error["msg"]))
        for error in exc.errors()
    )

    return PlainTextResponse(
        f"{_invalid_data(exc.title).current}\n{errors}", status_code=422
    )


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
    configure_logging()
    _validate_auth_settings()
    install_probe_access_filter()

    app = FastAPI(
        lifespan=lifespan,
        docs_url="/docs" if settings.security.expose_api_docs else None,
        redoc_url="/redoc" if settings.security.expose_api_docs else None,
        openapi_url="/openapi.json" if settings.security.expose_api_docs else None,
    )
    configure_observability(app)
    app.add_middleware(LanguageMiddleware)
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.include_router(public_router)
    app.include_router(api_router)
    if mcp_http_app is not None:
        app.mount("/mcp", mcp_http_app)
    return app
