"""Tests for the unauthenticated public routes."""

import pytest

from hivegent.config import settings
from hivegent.server.routes.public import config


async def test_config_reports_oidc_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/config` echoes the issuer and frontend client id from settings."""
    monkeypatch.setattr(settings.auth, "issuer", "https://idp.example.com/oidc")
    monkeypatch.setattr(settings.auth, "frontend_client_id", "hivegent-spa")

    result = await config()

    assert result.oidc.issuer_uri == "https://idp.example.com/oidc"
    assert result.oidc.client_id == "hivegent-spa"
