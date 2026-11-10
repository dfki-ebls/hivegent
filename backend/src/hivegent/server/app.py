"""FastAPI application assembly for Hivegent."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress

import sqlalchemy as sa
from fastapi import FastAPI

from ..agents import check_tool_settings
from ..config import API_PREFIX, API_VERSION, settings
from ..conversation_files import sweep_conversations
from ..db import apply_migrations, engine_lifespan
from ..entries import folds_case
from ..http_client import shared_http_client_lifespan
from ..logging_config import configure_logging
from ..mcp import mcp_app
from ..observability import configure_observability
from ..reconcile import reconcile_all
from ..retrieval import reconcile_index_state, warm_index
from ..sandbox import monty_pool_lifespan
from ..workers.pool import pipeline_pool
from .access_log import install_probe_access_filter
from .language import LanguageMiddleware
from .maintenance import load_persisted_state
from .operations import cleanup_spool_dir
from .problems import install_problem_handlers
from .routes import api_router
from .routes.public import router as public_router
from .runs import runs

__all__ = ["create_app", "mcp_http_app"]

logger = logging.getLogger(__name__)

mcp_http_app = mcp_app.http_app(path="/") if settings.mcp.enable else None


async def _verify_fts_config() -> None:
    """Fail loudly when the live ``chunks.tsv`` configs differ from settings.

    ``alembic check`` does not compare generated-column expressions, so a
    changed ``settings.embedding.text_search_config`` without a follow-up
    migration would silently desync the stored ``tsvector`` from the
    query-side ``tsquery``.  We read the live generated expression and
    compare the ordered ``to_tsvector`` configurations to the configured
    ones at boot.
    """
    from ..db.engine import get_engine

    expected = settings.embedding.text_search_configs
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


async def _housekeep(interval: float) -> None:
    """Sweep the conversation folders now, then once every *interval* seconds."""
    while True:
        try:
            _ = await sweep_conversations()
        except Exception:
            logger.warning("Sweeping the conversation folders failed", exc_info=True)

        await asyncio.sleep(interval)


@asynccontextmanager
async def _housekeeping() -> AsyncGenerator[None]:
    """Run the cleanups in the background for as long as the app does.

    Never awaited by the boot: the first pass walks every conversation's folder,
    which the requests the app starts serving meanwhile do not wait on.
    """
    task = asyncio.create_task(
        _housekeep(settings.conversations.sweep_interval_hours * 3600)
    )

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
        await background.enter_async_context(_housekeeping())
        # Unwound ahead of the engine, so a turn the shutdown stops is stored.
        background.push_async_callback(runs.aclose)
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


def _validate_auth_settings() -> None:
    if settings.auth.enable:
        if not settings.auth.issuer:
            raise ValueError(
                "Authentication is enabled but HIVEGENT_AUTH__ISSUER is unset."
            )
        if not settings.auth.audience:
            raise ValueError(
                "Authentication is enabled but HIVEGENT_AUTH__AUDIENCE is empty. "
                "Set it to the client IDs allowed to call the API (token audiences)."
            )
        if settings.mcp.enable and not settings.mcp.client_id:
            raise ValueError(
                "MCP is enabled but HIVEGENT_MCP__CLIENT_ID is empty, which would "
                "accept a token the issuer minted for any other client."
            )
        if folds_case(settings.data_dir):
            raise ValueError(
                "HIVEGENT_DATA_DIR is on a case-insensitive filesystem, where user "
                "and group IDs differing only in case would share one workspace. "
                "Use a case-sensitive volume while authentication is enabled."
            )

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

    if not settings.accept_terms:
        raise ValueError(
            "Read the additional terms of use (TERMS) and accept them "
            "by setting HIVEGENT_ACCEPT_TERMS=true."
        )

    _validate_auth_settings()
    install_probe_access_filter()

    # Under the API prefix, the only path the reverse proxy hands the backend.
    docs = settings.security.expose_api_docs
    app = FastAPI(
        title="Hivegent",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=f"{API_PREFIX}/redoc" if docs else None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
    )
    configure_observability(app)
    app.add_middleware(LanguageMiddleware)
    install_problem_handlers(app)
    app.include_router(public_router)
    app.include_router(api_router)
    if mcp_http_app is not None:
        app.mount("/mcp", mcp_http_app)
    return app
