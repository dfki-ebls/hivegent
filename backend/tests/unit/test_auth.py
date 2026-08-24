"""Unit tests for OIDC discovery and JWKS resolution."""

from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from fastapi import HTTPException
from joserfc.jwk import KeySet, OctKey

from hivegent import auth
from hivegent.auth import (
    GroupClaim,
    JWKSFetcher,
    build_discovery_url,
    parse_group_claims,
)
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        requested_urls.append(url)
        if url == discovery_url:
            return httpx2.Response(200, json=discovery_payload)
        if url == custom_jwks_uri:
            return httpx2.Response(200, json=jwks_payload)
        raise AssertionError(f"unexpected URL {url!r}")

    def fake_sync_get(url: Any, **_: Any) -> httpx2.Response:
        with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
            return client.get(str(url))

    monkeypatch.setattr(httpx2, "get", fake_sync_get)

    fetcher = JWKSFetcher()
    mock_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(auth, "get_trusted_http_client", lambda: mock_client)

    try:
        await fetcher.get_jwks()
    finally:
        await mock_client.aclose()

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
    monkeypatch.setattr(settings.auth, "audience", [])

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


async def test_get_jwks_serves_stale_keys_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed JWKS refresh must fall back to the cached key set.

    A transient IdP outage at cache-TTL expiry would otherwise surface as
    a 503 on whatever user request happened to trigger the refresh.
    """
    fetcher = JWKSFetcher()
    stale = KeySet.import_key_set({"keys": [OctKey.generate_key().as_dict()]})
    fetcher._jwks._value = stale
    fetcher._jwks._time = 0  # long expired

    async def fake_get_discovery(force_refresh: bool = False) -> SimpleNamespace:
        return SimpleNamespace(jwks_uri="https://idp.example.com/jwks")

    monkeypatch.setattr(fetcher, "_get_discovery", fake_get_discovery)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("idp unreachable", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(auth, "get_trusted_http_client", lambda: client)

    try:
        assert await fetcher.get_jwks() is stale
        # The failed refresh re-stamps the cache, so the retry window is a
        # full TTL and the next call never reaches the IdP.
        assert await fetcher.get_jwks() is stale
    finally:
        await client.aclose()


def _install_fake_jwt_pipeline(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]
) -> None:
    """Stub JWKS/algorithm fetching and ``jwt.decode`` for claim validation."""
    fetcher = JWKSFetcher()
    fake_keyset = KeySet.import_key_set({"keys": [OctKey.generate_key().as_dict()]})

    async def fake_get_jwks(force_refresh: bool = False) -> KeySet:
        return fake_keyset

    async def fake_get_algorithms(force_refresh: bool = False) -> list[str]:
        return ["EdDSA"]

    monkeypatch.setattr(fetcher, "get_jwks", fake_get_jwks)
    monkeypatch.setattr(fetcher, "get_allowed_algorithms", fake_get_algorithms)
    monkeypatch.setattr(auth, "_jwks_fetcher", fetcher)

    def fake_decode(value: Any, key: Any, algorithms: Any = None, **_: Any) -> Any:
        return SimpleNamespace(claims=claims)

    monkeypatch.setattr(auth.jwt, "decode", fake_decode)


@pytest.mark.parametrize(
    ("configured_iss", "token_iss"),
    [
        ("https://auth.example.com", "https://auth.example.com/"),
        ("https://auth.example.com/", "https://auth.example.com"),
        ("https://auth.example.com/", "https://auth.example.com/"),
        ("https://auth.example.com", "https://auth.example.com"),
    ],
)
async def test_validate_jwt_token_accepts_trailing_slash_iss_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    configured_iss: str,
    token_iss: str,
) -> None:
    """A trailing ``/`` on ``iss`` must not cause a rejection either way."""
    monkeypatch.setattr(settings.auth, "issuer", configured_iss)
    monkeypatch.setattr(settings.auth, "audience", [])

    _install_fake_jwt_pipeline(monkeypatch, {"sub": "user-1", "iss": token_iss})

    user = await auth.validate_jwt_token("dummy.jwt.token")
    assert user.id == "user-1"


async def test_validate_jwt_token_rejects_mismatched_iss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad ``iss`` produces a 401 that names the claim but leaks no values."""
    monkeypatch.setattr(settings.auth, "issuer", "https://auth.example.com")
    monkeypatch.setattr(settings.auth, "audience", [])

    _install_fake_jwt_pipeline(
        monkeypatch,
        {"sub": "user-1", "iss": "https://evil.example.com"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth.validate_jwt_token("dummy.jwt.token")

    assert exc_info.value.status_code == 401
    detail = exc_info.value.detail
    assert isinstance(detail, str)
    assert "iss" in detail
    assert "https://auth.example.com" not in detail
    assert "https://evil.example.com" not in detail


async def test_validate_jwt_token_rejects_mismatched_aud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad ``aud`` produces a 401 that names the claim but leaks no values."""
    monkeypatch.setattr(settings.auth, "issuer", "")
    monkeypatch.setattr(settings.auth, "audience", ["hivegent-api"])

    _install_fake_jwt_pipeline(monkeypatch, {"sub": "user-1", "aud": "other-api"})

    with pytest.raises(HTTPException) as exc_info:
        await auth.validate_jwt_token("dummy.jwt.token")

    assert exc_info.value.status_code == 401
    detail = exc_info.value.detail
    assert isinstance(detail, str)
    assert "aud" in detail
    assert "hivegent-api" not in detail
    assert "other-api" not in detail


async def test_validate_jwt_token_accepts_audience_by_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``*`` audience pattern accepts any client id with that prefix."""
    monkeypatch.setattr(settings.auth, "issuer", "")
    monkeypatch.setattr(settings.auth, "audience", ["hivegent-*"])

    _install_fake_jwt_pipeline(
        monkeypatch, {"sub": "svc", "aud": "hivegent-integration"}
    )

    user = await auth.validate_jwt_token("dummy.jwt.token")
    assert user.id == "svc"


async def test_admin_is_a_role_not_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin derives from the roles claim; the same name in groups must not grant it."""
    monkeypatch.setattr(settings.auth, "issuer", "")
    monkeypatch.setattr(settings.auth, "audience", [])

    # "admin" only in the groups claim is an ordinary group, not a privilege.
    _install_fake_jwt_pipeline(
        monkeypatch, {"sub": "u", "groups": ["admin", "eng:write"]}
    )
    user = await auth.validate_jwt_token("t")
    assert not user.is_admin
    assert "admin" in user.all_groups
    assert "eng" in user.write_groups

    # "admin" in the roles claim grants the privilege without joining a group.
    _install_fake_jwt_pipeline(monkeypatch, {"sub": "u", "roles": ["admin"]})
    user = await auth.validate_jwt_token("t")
    assert user.is_admin
    assert user.roles == frozenset({"admin"})
    assert not user.all_groups


def test_resolve_claim_path_walks_nested_mappings() -> None:
    """A dotted path descends into nested mappings; a plain name stays flat."""
    claims = {"groups": ["a"], "custom": {"groups": ["team-kb:read"]}}
    assert auth._resolve_claim_path(claims, "groups") == ["a"]
    assert auth._resolve_claim_path(claims, "custom.groups") == ["team-kb:read"]
    assert auth._resolve_claim_path(claims, "custom.missing") is None
    assert auth._resolve_claim_path(claims, "groups.nope") is None


def test_default_claim_paths_union_top_level_and_custom() -> None:
    """By default, groups and roles are read from both the top-level and ``custom``-nested
    claim and unioned — serving interactive users and client-credentials bots at once."""
    user = {"groups": ["eng:write"], "roles": ["admin"]}
    bot = {"custom": {"groups": ["team-kb:read"], "roles": ["auditor"]}}
    both = {"groups": ["eng:write"], "custom": {"groups": ["team-kb:read"]}}

    def parsed(claims: dict[str, Any]) -> dict[str, str | None]:
        entries = auth._claim_entries(claims, settings.claims.groups)
        return {c.id: c.permission for c in parse_group_claims(entries)}

    assert parsed(user) == {"eng": "write"}
    assert parsed(bot) == {"team-kb": "read"}
    assert parsed(both) == {"eng": "write", "team-kb": "read"}
    assert auth._extract_roles(user) == frozenset({"admin"})
    assert auth._extract_roles(bot) == frozenset({"auditor"})


async def test_validate_jwt_token_does_not_leak_sub_in_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sub`` is a user identifier and must never appear in error details."""
    monkeypatch.setattr(settings.auth, "issuer", "")
    monkeypatch.setattr(settings.auth, "audience", ["hivegent-api"])

    secret_sub = "personally-identifying-subject-id"
    _install_fake_jwt_pipeline(monkeypatch, {"sub": secret_sub, "aud": "other-api"})

    with pytest.raises(HTTPException) as exc_info:
        await auth.validate_jwt_token("dummy.jwt.token")

    detail = exc_info.value.detail
    assert isinstance(detail, str)
    assert secret_sub not in detail


async def test_impersonation_requires_admin() -> None:
    """A non-admin sending the impersonation header must get 403."""
    actor = auth.User(id="u", roles=frozenset({"editor"}))

    with pytest.raises(HTTPException) as exc_info:
        await auth._impersonate(actor, "victim")

    assert exc_info.value.status_code == 403


async def test_impersonation_rejects_malformed_target() -> None:
    """Targets failing user-id sanitisation must yield 400, not a lookup."""
    admin = auth.User(id="root", roles=frozenset({"admin"}))

    with pytest.raises(HTTPException) as exc_info:
        await auth._impersonate(admin, "../etc/passwd")

    assert exc_info.value.status_code == 400


async def test_impersonation_yields_personal_scope_without_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin gets the target's id only; unknown targets yield 404."""

    async def fake_exists(user_id: str) -> bool:
        return user_id == "victim"

    monkeypatch.setattr(auth, "user_exists", fake_exists)
    admin = auth.User(id="root", roles=frozenset({"admin"}))

    user = await auth._impersonate(admin, "victim")
    assert user == auth.User(id="victim")
    # Groups live only in the target's OIDC token, never reconstructed here.
    assert not user.all_groups
    assert not user.is_admin

    with pytest.raises(HTTPException) as exc_info:
        await auth._impersonate(admin, "ghost")
    assert exc_info.value.status_code == 404


def _parsed_group_claims(entries: list[Any]) -> list[GroupClaim]:
    return list(parse_group_claims(entries))


def test_string_entries_split_off_the_permission_suffix() -> None:
    assert _parsed_group_claims(["eng:write", "sales:read", "ops"]) == [
        GroupClaim("eng", permission="write"),
        GroupClaim("sales", permission="read"),
        GroupClaim("ops"),
    ]


def test_scim_objects_carry_id_and_display() -> None:
    """RFC 7643 §4.1.2 shape: the ID is verbatim, so a ``:`` in it survives."""
    assert _parsed_group_claims([{"value": "9uh:8ei", "display": "Engineering"}]) == [
        GroupClaim("9uh:8ei", "Engineering")
    ]


def test_malformed_entries_are_skipped() -> None:
    assert _parsed_group_claims([":write", "", 42, {"display": "x"}]) == []
