"""Tests for SSRF-sensitive networking helpers."""

import httpx
import pytest
from starlette.testclient import TestClient

from hivegent.config import settings
from hivegent.security import create_safe_async_client


async def test_safe_async_client_blocks_private_ip_connections() -> None:
    """The safe transport rejects private addresses at connection time."""
    async with create_safe_async_client(timeout=0.1) as client:
        with pytest.raises(httpx.ConnectError, match="private or reserved"):
            await client.get("http://127.0.0.1:1")


def test_auth_disabled_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-disabled mode must not start by accident."""
    # Import while auth is enabled so the module-level ``app = create_app()``
    # in ``server/app.py`` doesn't raise during test collection.
    monkeypatch.setattr(settings.auth, "enable", True)
    monkeypatch.setattr(settings.auth, "allow_disabled", False)
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings.auth, "enable", False)
    with pytest.raises(ValueError, match="Authentication is disabled"):
        create_app()


def test_openapi_docs_are_disabled_by_default(app_client: TestClient) -> None:
    """OpenAPI and interactive docs should not be public by default."""
    assert app_client.get("/openapi.json").status_code == 404
    assert app_client.get("/docs").status_code == 404
    assert app_client.get("/redoc").status_code == 404


def test_mcp_is_not_mounted_by_default(app_client: TestClient) -> None:
    """The MCP HTTP surface should be opt-in."""
    assert app_client.get("/mcp").status_code == 404
