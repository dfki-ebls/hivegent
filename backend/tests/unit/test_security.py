"""Tests for SSRF-sensitive networking helpers and auth guards."""

from io import BytesIO

import httpx
import httpx2
import pytest
from fastapi import HTTPException, UploadFile

from hivegent.config import InferenceProvider, settings
from hivegent.security import (
    UrlPolicy,
    create_legacy_safe_async_client,
    create_safe_async_client,
)
from hivegent.server.common import prepare_llm_config
from hivegent.server.operations import enforce_upload_size
from hivegent.types import LlmConfig, resolve_llm_config


async def test_safe_async_client_blocks_private_ip_connections() -> None:
    """The safe transport rejects private addresses at connection time."""
    # Pin the guard on regardless of the ambient ``allow_private_urls`` the
    # dev shell exports, so the test exercises the filter, not the env.
    async with create_safe_async_client(timeout=0.1, policy=UrlPolicy()) as client:
        with pytest.raises(httpx2.ConnectError, match="private or reserved"):
            await client.get("http://127.0.0.1:1")


async def test_legacy_safe_client_blocks_private_ip_connections() -> None:
    """The FastMCP compatibility client retains connect-time protection."""
    async with create_legacy_safe_async_client(
        timeout=0.1, policy=UrlPolicy()
    ) as client:
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


async def test_resolve_llm_config_is_idempotent_on_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-resolving a trusted config keeps it trusted (image captioning re-resolves)."""
    monkeypatch.setattr(settings.llm, "base_url", "http://127.0.0.1:18000/v1")
    monkeypatch.setattr(settings.llm, "aux_model", "aux-model")
    monkeypatch.setattr(settings.llm, "inference_provider", InferenceProvider.VLLM)

    once = resolve_llm_config(LlmConfig())
    twice = resolve_llm_config(once)

    assert once.base_url_is_trusted is True
    assert once.inference_provider is InferenceProvider.VLLM
    assert twice.base_url == "http://127.0.0.1:18000/v1"
    assert twice.inference_provider is InferenceProvider.VLLM
    assert twice.base_url_is_trusted is True


def test_enforce_upload_size_rejects_over_limit() -> None:
    """Uploads whose parsed size exceeds the cap are rejected."""
    upload = UploadFile(file=BytesIO(b"abcd"), size=4, filename="big.md")

    with pytest.raises(HTTPException) as exc_info:
        enforce_upload_size(upload, limit=3, label="File")

    assert exc_info.value.status_code == 413
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
