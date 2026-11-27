"""Tests for SSRF-sensitive networking helpers and auth guards."""

import httpx2
import pytest
from fastapi import FastAPI
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
from hivegent.types import ChatRequest, Preferences
from tests.conftest import Configure
from tests.helpers import catalog

ISSUER = "https://auth.example.com"


def test_safe_async_client_requires_egress_proxy() -> None:
    """An untrusted client cannot silently fall back to direct networking."""
    with pytest.raises(ValueError, match="egress proxy"):
        create_safe_async_client(
            policy=UrlPolicy(allow_hosts=("example.com",)),
            proxy_url="",
            timeout=1.0,
        )


async def test_safe_async_client_checks_every_redirect_hop() -> None:
    """The host policy runs on each hop, so an allowed URL cannot redirect out."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "https://evil.example/final"})

    async with create_safe_async_client(
        policy=UrlPolicy(allow_hosts=("safe.example",)),
        proxy_url="http://127.0.0.1:4750",
        timeout=1.0,
        follow_redirects=True,
        transport=httpx2.MockTransport(handler),
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


@pytest.fixture()
def llm_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm", catalog())


@pytest.mark.usefixtures("llm_catalog")
def test_requests_name_catalog_models_only() -> None:
    """A request picks a chat model by id, and the endpoint stays the operator's."""
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


@pytest.mark.parametrize(
    ("accept_terms", "auth", "error"),
    [
        (False, {}, "HIVEGENT_ACCEPT_TERMS"),
        (True, {"issuer": None}, "hivegent dev"),
        (True, {"audience": None}, "AUDIENCE"),
        (True, {"audience": ["hivegent-*"]}, "exact client IDs"),
    ],
    ids=["terms", "issuer", "audience", "wildcard"],
)
def test_the_server_refuses_to_start_unguarded(
    configure: Configure, accept_terms: bool, auth: dict[str, object], error: str
) -> None:
    """Terms accepted, an issuer, and exact audiences, since a pattern would admit any future client."""
    from hivegent.server.app import create_app

    configure(
        accept_terms=accept_terms, auth={"issuer": ISSUER, "audience": ["api"]} | auth
    )

    with pytest.raises(ValueError, match=error):
        create_app()


def test_only_dev_has_the_development_user_and_refuses_an_issuer(
    configure: Configure,
) -> None:
    from hivegent.auth import get_current_principal
    from hivegent.server.app import create_dev_app

    configure(accept_terms=True, auth={"issuer": None})

    assert get_current_principal in create_dev_app().dependency_overrides

    configure(accept_terms=True, auth={"issuer": ISSUER})

    with pytest.raises(ValueError, match="hivegent serve"):
        create_dev_app()


@pytest.mark.parametrize(
    ("mcp", "dev", "error"),
    [
        ({"mode": "proxy", "client_id": None}, False, "CLIENT_SECRET"),
        ({"confirmation_key": None}, True, "confirmation_key"),
    ],
    ids=["proxy client", "confirmation key"],
)
def test_the_mcp_server_refuses_to_build_unguarded(
    configure: Configure, mcp: dict[str, object], dev: bool, error: str
) -> None:
    """The proxy pins upstream tokens to its own client, and approvals are signed with an explicit key, never a random one."""
    from hivegent.mcp import build_mcp_app

    configure(
        auth={"issuer": ISSUER},
        mcp={"base_url": HttpUrl("https://hivegent.example.com/mcp")} | mcp,
    )

    with pytest.raises(ValueError, match=error):
        build_mcp_app(dev=dev)


@pytest.mark.usefixtures("llm_catalog")
def test_a_stale_preference_falls_back_to_its_default() -> None:
    """A stored choice outlives what it names, such as a model the catalog dropped, without failing a turn."""
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
