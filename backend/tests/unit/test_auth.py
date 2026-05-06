"""Unit tests for OIDC discovery and JWKS resolution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from joserfc.jwk import KeySet, OctKey

from hivegent import auth
from hivegent.auth import JWKSFetcher, build_discovery_url
from hivegent.config import settings


async def test_get_jwks_uses_jwks_uri_from_discovery_doc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWKSFetcher must hit the discovery doc's ``jwks_uri`` verbatim."""
    issuer = "https://auth.example.com/auth/v1/oidc"
    custom_jwks_uri = f"{issuer}/certs"
    discovery_url = build_discovery_url(issuer)

    monkeypatch.setattr(settings.auth, "issuer", issuer)

    discovery_payload = {"issuer": issuer, "jwks_uri": custom_jwks_uri}
    jwks_payload = {"keys": [OctKey.generate_key().as_dict()]}

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)
        if url == discovery_url:
            return httpx.Response(200, json=discovery_payload)
        if url == custom_jwks_uri:
            return httpx.Response(200, json=jwks_payload)
        raise AssertionError(f"unexpected URL {url!r}")

    def fake_sync_get(url: Any, **_: Any) -> httpx.Response:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.get(str(url))

    monkeypatch.setattr(httpx, "get", fake_sync_get)

    fetcher = JWKSFetcher()
    monkeypatch.setattr(
        fetcher,
        "_get_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await fetcher.get_jwks()

    assert requested_urls == [discovery_url, custom_jwks_uri]


async def test_validate_jwt_token_passes_discovery_algorithms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``validate_jwt_token`` must forward the discovery algs to ``jwt.decode``.

    Regression: joserfc's default registry only permits its ``recommended``
    algs (HS256/RS256/ES256), so EdDSA-only IdPs like rauthy were rejected
    with ``Algorithm of 'EdDSA' is not recommended`` until we started
    passing ``algorithms=`` explicitly.
    """
    monkeypatch.setattr(settings.auth, "issuer", "")
    monkeypatch.setattr(settings.auth, "audience", None)

    fake_keyset = KeySet.import_key_set({"keys": [OctKey.generate_key().as_dict()]})
    fake_config = SimpleNamespace(
        id_token_signing_alg_values_supported=["EdDSA"],
        jwks_uri="https://example.invalid/jwks",
    )

    fetcher = JWKSFetcher()

    async def fake_get_jwks(force_refresh: bool = False) -> KeySet:
        return fake_keyset

    async def fake_get_discovery(force_refresh: bool = False) -> SimpleNamespace:
        return fake_config

    monkeypatch.setattr(fetcher, "get_jwks", fake_get_jwks)
    monkeypatch.setattr(fetcher, "_get_discovery", fake_get_discovery)
    monkeypatch.setattr(auth, "_jwks_fetcher", fetcher)

    captured: dict[str, Any] = {}

    def fake_decode(value: Any, key: Any, algorithms: Any = None, **_: Any) -> Any:
        captured["algorithms"] = algorithms
        return SimpleNamespace(claims={"sub": "user-1"})

    monkeypatch.setattr(auth.jwt, "decode", fake_decode)

    user = await auth.validate_jwt_token("dummy.jwt.token")

    assert captured["algorithms"] == ["EdDSA"]
    assert user.id == "user-1"
