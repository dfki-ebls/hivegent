"""Tests for SSRF-sensitive networking helpers and auth guards."""

from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from hivegent.config import settings
from hivegent.security import create_safe_async_client
from hivegent.server.common import prepare_llm_config
from hivegent.server.operations import read_upload_file
from hivegent.types import LlmConfig


async def test_safe_async_client_blocks_private_ip_connections() -> None:
    """The safe transport rejects private addresses at connection time."""
    # Pin the guard on regardless of the ambient ``allow_private_urls`` the
    # dev shell exports, so the test exercises the filter, not the env.
    async with create_safe_async_client(timeout=0.1, allow_private=False) as client:
        with pytest.raises(httpx.ConnectError, match="private or reserved"):
            await client.get("http://127.0.0.1:1")


async def test_prepare_llm_config_trusts_configured_base_url_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server-configured LLM URLs bypass only the user URL policy."""
    monkeypatch.setattr(settings.security, "allow_private_urls", False)
    monkeypatch.setattr(settings.llm, "model", "configured-model")
    monkeypatch.setattr(settings.llm, "api_key", "")
    monkeypatch.setattr(settings.llm, "base_url", "http://127.0.0.1:18000/v1")

    resolved = await prepare_llm_config(LlmConfig())

    assert resolved.base_url == "http://127.0.0.1:18000/v1"
    assert resolved.base_url_is_trusted is True

    monkeypatch.setattr(settings.llm, "base_url", "")

    with pytest.raises(HTTPException) as exc_info:
        await prepare_llm_config(
            LlmConfig(model="user-model", base_url="http://127.0.0.1:18000/v1")
        )

    assert exc_info.value.status_code == 400
    assert "Unsafe LLM base_url" in str(exc_info.value.detail)


async def test_read_upload_file_rejects_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-file uploads must be capped while streaming from the request."""
    monkeypatch.setattr(settings.limits, "max_file_size_bytes", 3)
    monkeypatch.setattr(settings.limits, "upload_read_chunk_size", 2)
    upload = UploadFile(file=BytesIO(b"abcd"), filename="big.md")

    with pytest.raises(HTTPException) as exc_info:
        await read_upload_file(upload)

    assert exc_info.value.status_code == 400
    assert "File too large" in str(exc_info.value.detail)


def test_auth_disabled_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-disabled mode must not start by accident."""
    monkeypatch.setattr(settings.auth, "enable", True)
    monkeypatch.setattr(settings.auth, "allow_disabled", False)
    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")
    monkeypatch.setattr(settings.auth, "audience", "hivegent-api")
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings.auth, "enable", False)
    with pytest.raises(ValueError, match="Authentication is disabled"):
        create_app()


def test_auth_enabled_requires_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth-enabled mode must pin tokens to the API audience."""
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings.auth, "enable", True)
    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")
    monkeypatch.setattr(settings.auth, "audience", None)

    with pytest.raises(ValueError, match="AUDIENCE"):
        create_app()
