"""Unit tests for OIDC discovery and JWKS resolution."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from joserfc.jwk import OctKey

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
