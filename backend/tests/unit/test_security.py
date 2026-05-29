"""Tests for SSRF-sensitive networking helpers and auth guards."""

import httpx
import pytest

from hivegent.config import settings
from hivegent.security import create_safe_async_client


async def test_safe_async_client_blocks_private_ip_connections() -> None:
    """The safe transport rejects private addresses at connection time."""
    # Pin the guard on regardless of the ambient ``allow_private_urls`` the
    # dev shell exports, so the test exercises the filter, not the env.
    async with create_safe_async_client(timeout=0.1, allow_private=False) as client:
        with pytest.raises(httpx.ConnectError, match="private or reserved"):
            await client.get("http://127.0.0.1:1")


def test_auth_disabled_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-disabled mode must not start by accident."""
    monkeypatch.setattr(settings.auth, "enable", True)
    monkeypatch.setattr(settings.auth, "allow_disabled", False)
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings.auth, "enable", False)
    with pytest.raises(ValueError, match="Authentication is disabled"):
        create_app()
