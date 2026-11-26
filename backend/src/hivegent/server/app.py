"""FastAPI application assembly for Hivegent."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

from fastapi import FastAPI
from starlette.middleware import Middleware

from ..agents import check_tool_settings
from ..agents.integrations import integrations_lifespan
from ..auth import dev_user, get_current_principal
from ..config import API_PREFIX, API_VERSION, settings
from ..conversation_files import sweep_conversations
from ..db import apply_migrations, engine_lifespan, verify_schema
from ..db.engine import serving
from ..delegations import delegations
from ..files import reset_transient
from ..http_client import shared_http_client_lifespan
from ..logging_config import configure_logging
from ..mcp import McpApp, build_mcp_app
from ..observability import configure_observability
from ..pipelines import POOL, check_pipelines
from ..reconcile import reconcile_all
from ..retrieval import reconcile_index_state, warm_index
from ..runs import runs
from ..sandbox import monty_pool_lifespan
from ..shared.tasks import background
from ..sources.sync import prune_sources, sources_lifespan
from ..store import HostGrant
from ..tools.changes import ChangeRefused
from ..tools.entries import folds_case
from ..workspace import recover_changesets, sweep_partials
from .access_log import install_probe_access_filter
from .language import LanguageMiddleware
from .maintenance import load_persisted_state
from .no_store import NoStoreMiddleware
from .problems import install_problem_handlers
from .rate_limit import McpAdmission
from .routes import api_router
from .routes.public import router as public_router

__all__ = ["create_app", "create_dev_app"]

logger = logging.getLogger(__name__)


async def _housekeep(interval: float) -> None:
    """Sweep the conversation folders and the partial files of interrupted writes now, then once every *interval* seconds."""
    while True:
        try:
            _ = await sweep_conversations()
        except Exception:
            logger.warning("Sweeping the conversation folders failed", exc_info=True)

        try:
            _ = await asyncio.to_thread(sweep_partials, settings.data_dir)
        except Exception:
            logger.warning("Sweeping the partial files failed", exc_info=True)

        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI, mcp: McpApp | None) -> AsyncGenerator[None]:
    """Open shared resources and delegate to MCP."""
    # Before anything is opened: a misspelled exclusion withholds nothing, and
    # the boot that would have surfaced it is the cheapest place to say so.
    # The pipeline options are checked here rather than in `config`, whose
    # validators cannot import the pipelines that import it.
    check_tool_settings()
    check_pipelines()

    async with engine_lifespan(), AsyncExitStack() as stack:
        # Held from the first query on, so a second server or `hivegent reset`
        # refuses while it serves.
        await stack.enter_async_context(serving())
        # Only once it serves alone, and before anything starts a temporary file.
        await asyncio.to_thread(reset_transient)
        await stack.enter_async_context(monty_pool_lifespan())
        await stack.enter_async_context(shared_http_client_lifespan())
        # Load the embedding model now so the first upload after boot does not
        # pay the sentence-transformers cold-start latency inline.
        warm = asyncio.create_task(warm_index())
        await stack.enter_async_context(background(warm))
        # Before any commit, sweep, or reconciliation sees a half-made change.
        await recover_changesets()
        await apply_migrations()
        await verify_schema()
        # Never awaited by the boot: the first pass walks every conversation's
        # folder and the whole data directory, which the requests the app
        # starts serving meanwhile do not wait on.
        sweeper = asyncio.create_task(
            _housekeep(settings.conversations.sweep_interval_hours * 3600)
        )
        await stack.enter_async_context(background(sweeper))
        await stack.enter_async_context(integrations_lifespan())
        # Unwound ahead of the engine, so a turn the shutdown stops is stored,
        # and the tasks first, so none wakes its conversation meanwhile.
        stack.push_async_callback(runs.aclose)
        stack.push_async_callback(delegations.aclose)
        await load_persisted_state()
        await reconcile_index_state()
        await warm
        # Before the reconcile, which would otherwise index a mirror no source owns.
        await prune_sources()
        # Refused, and logged, while a change a rollback could not finish is kept.
        try:
            reports = await reconcile_all(HostGrant())
        except ChangeRefused:
            pass
        except Exception:
            logger.warning("Startup reconciliation failed", exc_info=True)
        else:
            for key, synced in reports.items():
                logger.info("Reconciled %s: %d entries synced", key, synced)
        # Only once the index matches the embedding model, so no sync indexes
        # into a column the index reconcile is still rebuilding.
        await stack.enter_async_context(sources_lifespan())
        try:
            if mcp is None:
                yield
            else:
                async with mcp.app.lifespan(app):
                    yield
        finally:
            # Tear down the persistent converter and chunker worker processes,
            # a no-op if the pool was never used.
            POOL.shutdown(wait=False, cancel_futures=True)


def _validate_auth_settings(*, dev: bool) -> None:
    """Refuse an incomplete issuer configuration before serving.

    ``hivegent serve`` always authenticates, and ``hivegent dev`` never does,
    so neither runs with an issuer it would not honour.
    """
    if dev:
        if settings.auth.enabled:
            raise ValueError(
                "hivegent dev acts as the development user and validates no "
                "token. Unset HIVEGENT_AUTH__ISSUER or run hivegent serve."
            )

        logger.warning(
            "AUTH DISABLED by hivegent dev. Every request acts as the dev user "
            "'localhost' with write access to every group on disk."
        )

        return

    if settings.auth.issuer is None:
        raise ValueError(
            "hivegent serve always authenticates, but HIVEGENT_AUTH__ISSUER is "
            "empty. Set it, or run hivegent dev for local development."
        )

    if not settings.auth.audience:
        raise ValueError(
            "HIVEGENT_AUTH__ISSUER is set but HIVEGENT_AUTH__AUDIENCE is empty. "
            "Set it to the client IDs allowed to call the API (token audiences)."
        )

    if any("*" in audience for audience in settings.auth.audience):
        raise ValueError(
            "HIVEGENT_AUTH__AUDIENCE takes exact client IDs, wildcards are "
            "no longer supported."
        )

    if folds_case(settings.data_dir):
        raise ValueError(
            "HIVEGENT_DATA_DIR is on a case-insensitive filesystem, where user "
            "and group IDs differing only in case would share one workspace. "
            "Use a case-sensitive volume while authentication is enabled."
        )


def create_app() -> FastAPI:
    """Create the application ``hivegent serve`` runs, which validates every token."""
    return _create_app(dev=False)


def create_dev_app() -> FastAPI:
    """Create the application ``hivegent dev`` runs, where every caller is the development user."""
    return _create_app(dev=True)


def _create_app(*, dev: bool) -> FastAPI:
    """Create the configured FastAPI application.

    Edge concerns, TLS, CORS, security headers, body-size caps, and
    compression, live in the reverse proxy (Caddy).  The backend keeps domain
    logic, auth, per-user rate limits, and observability.
    """
    configure_logging()

    if not settings.accept_terms:
        raise ValueError(
            "Read the additional terms of use (TERMS) and accept them "
            "by setting HIVEGENT_ACCEPT_TERMS=true."
        )

    _validate_auth_settings(dev=dev)
    install_probe_access_filter()
    # Admitted like the REST API, behind FastMCP's authentication.
    admission = Middleware(McpAdmission, principal=dev_user if dev else None)
    mcp = (
        build_mcp_app(dev=dev, middleware=[admission]) if settings.mcp.enabled else None
    )

    # Under the API prefix, the only path the reverse proxy hands the backend.
    docs = settings.security.expose_api_docs
    app = FastAPI(
        title="Hivegent",
        version=API_VERSION,
        lifespan=partial(lifespan, mcp=mcp),
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=f"{API_PREFIX}/redoc" if docs else None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
        # The most any upload route takes, one file beside two fields, so a
        # form never parses more.  Each upload route bounds its body itself.
        form_limits={"max_files": 1, "max_fields": 2},
    )
    configure_observability(app)
    app.add_middleware(LanguageMiddleware)
    app.add_middleware(NoStoreMiddleware)
    install_problem_handlers(app)
    app.include_router(public_router)
    app.include_router(api_router)

    if dev:
        app.dependency_overrides[get_current_principal] = dev_user

    if mcp is not None:
        app.router.routes.extend(mcp.routes)

    return app
