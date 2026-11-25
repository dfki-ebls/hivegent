"""Tests for the unauthenticated public routes."""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from hivegent.auth import get_current_principal, get_current_user
from hivegent.config import API_PREFIX, settings
from hivegent.server.routes import api_router, people_router
from hivegent.server.routes.conversations import chat_router
from hivegent.server.routes.public import config
from hivegent.server.routes.public import router as public_router

# The only endpoints reachable without authentication. The SPA fetches health
# and config before login, and the key set verifies signed reports and exports
# for people without a sign-in, so widening this set silently exposes the backend.
PUBLIC_PATHS = frozenset(
    {"/api/v1/health", "/api/v1/config", "/api/v1/transparency/jwks"}
)


async def test_config_reports_oidc_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/v1/config` echoes the issuer, frontend client id, and handbook from settings."""
    monkeypatch.setattr(settings.auth, "issuer", "https://idp.example.com/oidc")
    monkeypatch.setattr(settings.auth, "frontend_client_id", "hivegent-spa")
    monkeypatch.setattr(settings, "docs_url", "/docs/")

    result = await config()

    assert result.oidc is not None
    assert result.oidc.issuer_uri == "https://idp.example.com/oidc"
    assert result.oidc.client_id == "hivegent-spa"
    assert result.docs_url == "/docs/"


async def test_config_without_issuer_serves_no_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an issuer the SPA is told to mock the development user."""
    monkeypatch.setattr(settings.auth, "issuer", None)

    assert (await config()).oidc is None


def test_only_the_listed_routes_are_public() -> None:
    """Auth covers the whole API except the public routes.

    ``api_router`` authenticates every route it carries through a single
    router-level ``get_current_principal`` dependency that FastAPI cannot strip
    from a child route, so the invariant reduces to two checks: the public
    router exposes only :data:`PUBLIC_PATHS`, and that dependency is in place.
    """
    public_paths = {r.path for r in public_router.routes if isinstance(r, APIRoute)}
    assert public_paths == PUBLIC_PATHS

    api_dependencies = {depends.dependency for depends in api_router.dependencies}
    assert get_current_principal in api_dependencies


def test_a_service_principal_reaches_only_running_and_following_a_turn() -> None:
    """Every router sits behind ``get_current_user`` but the one holding the turn routes."""
    people = {depends.dependency for depends in people_router.dependencies}
    assert get_current_user in people

    assert [
        (r.path, r.methods) for r in chat_router.routes if isinstance(r, APIRoute)
    ] == [
        ("/conversations/{conversation_id}/chat", {"POST"}),
        ("/conversations/{conversation_id}/stream", {"GET"}),
    ]


def test_debug_routes_exist_only_when_enabled() -> None:
    """The debugging consoles are not registered under the default ``debug.enable``."""
    assert not settings.debug.enable
    app = FastAPI()
    app.include_router(api_router)

    assert not any(
        path.startswith(f"{API_PREFIX}/debug") for path in app.openapi()["paths"]
    )
