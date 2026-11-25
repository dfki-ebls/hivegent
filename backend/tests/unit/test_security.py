"""Tests for SSRF-sensitive networking helpers and auth guards."""

from io import BytesIO

import httpx2
import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import HttpUrl, ValidationError
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from hivegent import security
from hivegent.config import settings
from hivegent.config.llm import LlmModelSettings, LlmSettings
from hivegent.llm_config import aux_llm, chat_llm
from hivegent.security import (
    OperatorUrl,
    UrlPolicy,
    create_operator_client,
    create_safe_async_client,
)
from hivegent.server.no_store import NoStoreMiddleware
from hivegent.server.operations import enforce_upload_size
from hivegent.types import ChatRequest, Preferences
from hivegent.workspace.paths import file_too_large
from tests.helpers import catalog


@pytest.fixture(autouse=True)
def accepted_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept the additional terms so ``create_app`` reaches the auth checks."""
    monkeypatch.setattr(settings, "accept_terms", True)


def test_safe_async_client_requires_egress_proxy() -> None:
    """An untrusted client cannot silently fall back to direct networking."""
    with pytest.raises(ValueError, match="egress proxy"):
        create_safe_async_client(
            policy=UrlPolicy(allow_hosts=("example.com",)),
            proxy_url="",
            timeout=1.0,
        )


async def test_safe_async_client_checks_every_redirect_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The host policy runs on each hop, so an allowed URL cannot redirect out."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "https://evil.example/final"})

    monkeypatch.setattr(
        security, "_egress_transport", lambda *_: httpx2.MockTransport(handler)
    )

    async with create_safe_async_client(
        policy=UrlPolicy(allow_hosts=("safe.example",)),
        proxy_url="http://127.0.0.1:4750",
        timeout=1.0,
        follow_redirects=True,
    ) as client:
        with pytest.raises(security.UnsafeUrlError, match="allowlist"):
            await client.get("https://safe.example/start")


async def test_operator_client_keeps_no_cookies() -> None:
    """A client shared across callers never hands one caller's session to another."""
    seen: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get("cookie"))

        return httpx2.Response(200, headers={"set-cookie": "sid=1; Path=/"})

    client = create_operator_client(
        [OperatorUrl("https://crm.test/mcp")],
        timeout=httpx2.Timeout(1),
        headers={},
        unix_sockets={},
    )
    client._transport = httpx2.MockTransport(handler)

    for _ in range(2):
        _ = await client.get("https://crm.test/mcp")

    assert seen == [None, None]


def test_requests_name_catalog_models_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request picks a chat model by id, and the endpoint stays the operator's."""
    monkeypatch.setattr(settings, "llm", catalog())

    chat = chat_llm(Preferences(model="big").model)
    assert chat is not None
    assert (chat.model, chat.provider.base_url) == ("big-27b", "http://llmhop/v1")

    # The aux role falls back to the default, capped by both limits.
    aux = aux_llm()
    assert aux is not None
    assert aux.max_tokens == min(4096, settings.llm.aux_max_tokens or 4096)

    for unknown in ("whisper", "elsewhere"):
        with pytest.raises(ValidationError):
            Preferences(model=unknown)

    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {"id": "c", "messages": [], "llm": {"base_url": "https://evil.example"}}
        )


def test_catalog_references_are_checked() -> None:
    """A model naming a missing provider stops the boot."""
    with pytest.raises(ValidationError, match="unknown provider"):
        LlmSettings(models={"m": LlmModelSettings(provider="nowhere", model="m")})


def test_enforce_upload_size_rejects_over_limit() -> None:
    """Uploads whose parsed size exceeds the cap are rejected."""
    upload = UploadFile(file=BytesIO(b"abcd"), size=4, filename="big.md")

    with pytest.raises(HTTPException) as exc_info:
        enforce_upload_size(upload, limit=3, too_large=file_too_large)

    assert exc_info.value.status_code == 413
    assert "File too large" in str(exc_info.value.detail)


def test_terms_require_explicit_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server must not start before the operator accepts the additional terms."""
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings, "accept_terms", False)
    with pytest.raises(ValueError, match="HIVEGENT_ACCEPT_TERMS"):
        create_app()


def test_serve_requires_an_issuer_and_dev_refuses_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``hivegent dev`` has the development user, and it never validates tokens."""
    from hivegent.auth import get_current_principal
    from hivegent.server.app import create_app, create_dev_app

    monkeypatch.setattr(settings.auth, "issuer", None)

    with pytest.raises(ValueError, match="hivegent dev"):
        create_app()

    assert get_current_principal in create_dev_app().dependency_overrides

    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")

    with pytest.raises(ValueError, match="hivegent serve"):
        create_dev_app()


def test_auth_enabled_requires_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth-enabled mode must pin tokens to the API audience."""
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")
    monkeypatch.setattr(settings.auth, "audience", None)

    with pytest.raises(ValueError, match="AUDIENCE"):
        create_app()


def test_auth_rejects_audience_wildcards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audiences name exact clients, a pattern would admit any future one."""
    from hivegent.server.app import create_app

    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")
    monkeypatch.setattr(settings.auth, "audience", ["hivegent-*"])

    with pytest.raises(ValueError, match="exact client IDs"):
        create_app()


def test_mcp_proxy_requires_its_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP proxy must pin upstream tokens to its own client."""
    from hivegent.mcp import build_mcp_app

    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")
    monkeypatch.setattr(
        settings.mcp, "base_url", HttpUrl("https://hivegent.example.com/mcp")
    )
    monkeypatch.setattr(settings.mcp, "mode", "proxy")
    monkeypatch.setattr(settings.mcp, "client_id", None)

    with pytest.raises(ValueError, match="CLIENT_SECRET"):
        build_mcp_app()


def test_mcp_requires_its_confirmation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The approvals of ``/mcp`` are signed with an explicit key, never a random one."""
    from hivegent.mcp import build_mcp_app

    monkeypatch.setattr(settings.mcp, "confirmation_key", None)

    with pytest.raises(ValueError, match="confirmation_key"):
        build_mcp_app(dev=True)


def test_a_stale_preference_falls_back_to_its_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored choice outlives what it names, such as a model the catalog dropped, without failing a turn."""
    monkeypatch.setattr(settings, "llm", catalog())

    stored = Preferences.stored({"model": "gone", "mode": "read", "theme": "dark"})

    assert stored == Preferences(mode="read")


def test_responses_are_never_cached() -> None:
    """A response, a file one included, is kept out of every cache, since its URL names another account's data for the next caller."""
    app = FastAPI()
    app.add_middleware(NoStoreMiddleware)

    @app.get("/file")
    def file() -> PlainTextResponse:
        return PlainTextResponse("private", headers={"Last-Modified": "x"})

    assert TestClient(app).get("/file").headers["Cache-Control"] == "no-store"
