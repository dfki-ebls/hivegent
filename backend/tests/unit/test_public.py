"""Tests for the unauthenticated public routes."""

import pytest
from fastapi.routing import APIRoute

from hivegent.auth import get_current_user
from hivegent.config import settings
from hivegent.server.routes import api_router
from hivegent.server.routes.public import config
from hivegent.server.routes.public import router as public_router

# The only endpoints reachable without authentication. The SPA fetches both
# before login, so widening this set silently exposes the backend.
PUBLIC_PATHS = frozenset({"/api/health", "/api/config"})


async def test_config_reports_oidc_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/config` echoes the issuer and frontend client id from settings."""
    monkeypatch.setattr(settings.auth, "issuer", "https://idp.example.com/oidc")
    monkeypatch.setattr(settings.auth, "frontend_client_id", "hivegent-spa")

    result = await config()

    assert result.oidc.issuer_uri == "https://idp.example.com/oidc"
    assert result.oidc.client_id == "hivegent-spa"


def test_only_health_and_config_are_public() -> None:
    """Auth covers the whole API except the two public routes.

    ``api_router`` authenticates every route it carries through a single
    router-level ``get_current_user`` dependency that FastAPI cannot strip from
    a child route, so the invariant reduces to two checks: the public router
    exposes only health and config, and that dependency is in place. A new
    public route, or a dropped dependency, fails one of them.
    """
    public_paths = {r.path for r in public_router.routes if isinstance(r, APIRoute)}
    assert public_paths == PUBLIC_PATHS

    api_dependencies = {depends.dependency for depends in api_router.dependencies}
    assert get_current_user in api_dependencies
