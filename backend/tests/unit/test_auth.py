"""Unit tests for token verification and claim parsing."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from fastapi import HTTPException
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey
from pydantic import HttpUrl, SecretStr
from starlette.applications import Starlette

from hivegent import auth
from hivegent.auth import (
    PROBE,
    GroupClaim,
    JWKSFetcher,
    authenticate,
    build_discovery_url,
    check_kind,
    parse_group_claims,
    until_expiry,
    user_from_claims,
)
from hivegent.config import settings
from hivegent.mcp import build_mcp_app
from hivegent.mcp.app import HivegentVerifier
from hivegent.security import OperatorUrl
from hivegent.store import User

ISSUER = OperatorUrl("https://auth.example.com/oidc")
JWKS_URI = f"{ISSUER}/certs"
AUDIENCE = ["hivegent-api"]
KEY = RSAKey.generate_key(2048, parameters={"kid": "rsa"})
HMAC = OctKey.generate_key(parameters={"kid": "hmac"})

type Discovery = dict[str, Any]


def _claims(**changes: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "user-1",
        "aud": "hivegent-api",
        "iat": now,
        "exp": now + 300,
    }

    return {k: v for k, v in {**claims, **changes}.items() if v is not None}


def _token(claims: dict[str, Any], alg: str = "RS256") -> str:
    key = KEY if alg.startswith("RS") else HMAC
    algorithms = None if alg.startswith("RS") else [alg]

    return jwt.encode({"alg": alg, "kid": key.kid}, claims, key, algorithms=algorithms)


@pytest.fixture()
async def idp(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Callable[[Discovery], None]]:
    """Serve the issuer's discovery and keys, the former changed by the returned function."""
    discovery: Discovery = {
        "issuer": ISSUER,
        "jwks_uri": JWKS_URI,
        "id_token_signing_alg_values_supported": ["RS256", "HS256", "none"],
    }
    keys = {"keys": [KEY.as_dict(private=False), HMAC.as_dict()]}

    def handler(request: httpx2.Request) -> httpx2.Response:
        match str(request.url):
            case url if url == build_discovery_url(ISSUER):
                return httpx2.Response(200, json=discovery)
            case url if url == JWKS_URI:
                return httpx2.Response(200, json=keys)
            case url:
                raise AssertionError(f"unexpected URL {url!r}")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(auth, "get_trusted_http_client", lambda *_: client)
    monkeypatch.setattr(auth, "_jwks_fetcher", JWKSFetcher())
    monkeypatch.setattr(settings.auth, "issuer", ISSUER)

    async with client:
        yield discovery.update


async def test_a_signed_token_names_its_principal(
    idp: Callable[[Discovery], None],
) -> None:
    """A token of the issuer, named byte for byte, names its principal, and no other ``iss`` passes."""
    credential = await authenticate(_token(_claims()), AUDIENCE)

    assert credential.user.id == "user-1"

    with pytest.raises(HTTPException, match="iss"):
        await authenticate(_token(_claims(iss=f"{ISSUER}/")), AUDIENCE)


async def test_a_configured_issuer_differing_by_a_trailing_slash_verifies_the_discovered_one(
    idp: Callable[[Discovery], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both spellings address one discovery document, whose issuer the tokens carry."""
    monkeypatch.setattr(settings.auth, "issuer", OperatorUrl(f"{ISSUER}/"))

    assert (await authenticate(_token(_claims()), AUDIENCE)).user.id == "user-1"

    with pytest.raises(HTTPException, match="iss"):
        await authenticate(_token(_claims(iss=f"{ISSUER}/")), AUDIENCE)


@pytest.mark.parametrize("alg", ["HS256", "none"])
async def test_only_asymmetric_algorithms_are_accepted(
    idp: Callable[[Discovery], None], alg: str
) -> None:
    """Whatever discovery advertises, a token signed by a shared secret or unsigned is refused."""
    if alg == "none":
        header = jwt.encode({"alg": "RS256"}, _claims(), KEY).split(".")[1]
        token = f"eyJhbGciOiJub25lIn0.{header}."
    else:
        token = _token(_claims(), alg)

    with pytest.raises(HTTPException) as exc_info:
        await authenticate(token, AUDIENCE)

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        _claims(exp=None),
        _claims(iat=None),
        _claims(iss="https://evil.example.com"),
        _claims(aud="hivegent-api-x"),
        _claims(exp=int(time.time()) - 3600),
    ],
)
async def test_a_token_lacking_a_required_claim_is_refused(
    idp: Callable[[Discovery], None], claims: dict[str, Any]
) -> None:
    """The 401 names at most the claim, never the expected or received value."""
    with pytest.raises(HTTPException) as exc_info:
        await authenticate(_token(claims), AUDIENCE)

    detail = str(exc_info.value.detail)
    assert exc_info.value.status_code == 401
    assert (
        "evil" not in detail and "hivegent-api" not in detail and "user-1" not in detail
    )


@pytest.mark.parametrize(
    "discovery",
    [
        {"issuer": "https://evil.example.com"},
        {"jwks_uri": "https://evil.example.com/certs"},
        {"jwks_uri": "http://auth.example.com/oidc/certs"},
        {"id_token_signing_alg_values_supported": ["HS256"]},
    ],
)
async def test_a_foreign_discovery_document_is_refused(
    idp: Callable[[Discovery], None], discovery: Discovery
) -> None:
    """Another issuer, keys off the issuer's https origin, or no asymmetric algorithm."""
    idp(discovery)

    with pytest.raises(HTTPException) as exc_info:
        await authenticate(_token(_claims()), AUDIENCE)

    assert exc_info.value.status_code == 500


async def test_mcp_tokens_name_the_advertised_resource(
    idp: Callable[[Discovery], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/mcp`` verifies as the API does, for the resource its challenge and metadata advertise."""
    monkeypatch.setattr(settings.mcp, "base_url", HttpUrl("https://hivegent.test/mcp"))
    monkeypatch.setattr(settings.mcp, "mode", "remote")
    monkeypatch.setattr(settings.mcp, "client_id", None)
    monkeypatch.setattr(settings.mcp, "confirmation_key", SecretStr("k" * 32))
    app = Starlette(routes=build_mcp_app().routes)

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://hivegent.test"
    ) as client:
        challenge = (await client.post("/mcp")).headers["www-authenticate"]
        metadata_url = "https://hivegent.test/.well-known/oauth-protected-resource/mcp"
        assert f'resource_metadata="{metadata_url}"' in challenge
        resource = (await client.get(metadata_url)).json()["resource"]

    assert resource == "https://hivegent.test/mcp"
    verifier = HivegentVerifier([resource])
    assert await verifier.verify_token(_token(_claims())) is None
    token = await verifier.verify_token(_token(_claims(aud=resource)))
    assert token is not None and token.claims["sub"] == "user-1"


async def test_get_jwks_serves_stale_keys_when_refresh_fails() -> None:
    """A failed refresh falls back to the cached keys and re-stamps their TTL."""
    fetcher = JWKSFetcher()
    stale = auth._Keys(KeySet([KEY]), ["RS256"], ISSUER)
    fetcher._keys._value = stale
    fetcher._keys._time = 0
    calls: list[None] = []

    async def failing() -> auth._Keys:
        calls.append(None)
        raise HTTPException(503)

    fetcher._fetch = failing  # pyright: ignore[reportAttributeAccessIssue]

    assert await fetcher.get() is stale
    assert await fetcher.get() is stale
    assert len(calls) == 1


async def test_admin_is_a_role_not_a_group(idp: Callable[[Discovery], None]) -> None:
    """Admin derives from the roles claim, the same name in groups must not grant it."""
    user = (
        await authenticate(_token(_claims(groups=["admin", "eng:write"])), AUDIENCE)
    ).user
    assert not user.is_admin
    assert "admin" in user.all_groups
    assert "eng" in user.write_groups

    user = (await authenticate(_token(_claims(roles=["admin"])), AUDIENCE)).user
    assert user.is_admin
    assert not user.all_groups


async def test_a_stream_ends_when_its_token_expires() -> None:
    """Events flow until the expiry, then the stream ends without waiting for more."""

    async def events() -> AsyncIterator[int]:
        yield 1
        await asyncio.sleep(60)
        yield 2

    seen = [event async for event in until_expiry(events(), time.time() + 0.05)]

    assert seen == [1]


def test_claim_paths_walk_nested_mappings_and_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path descends through nested mappings, and the entries of every path are unioned."""
    claims = {"groups": ["eng:write"], "custom": {"groups": ["team-kb:read"]}}
    assert auth._resolve_claim_path(claims, ["groups", "nope"]) is None

    def parsed() -> dict[str, str | None]:
        entries = auth._claim_entries(claims, settings.claims.groups)
        return {c.id: c.permission for c in parse_group_claims(entries)}

    assert parsed() == {"eng": "write"}

    monkeypatch.setattr(settings.claims, "groups", [["groups"], ["custom", "groups"]])

    assert parsed() == {"eng": "write", "team-kb": "read"}


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


def test_the_service_client_is_a_service_whatever_it_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its groups and roles are never read, so it sees the global workspace alone."""
    monkeypatch.setattr(settings.auth, "service_client_id", "hivegent-bot")
    claims = {"sub": "bot", "groups": ["team:write"], "roles": ["admin"]}

    bot = user_from_claims({**claims, "azp": "hivegent-bot"})
    person = user_from_claims({**claims, "azp": "hivegent-spa"})

    assert bot.is_service and not bot.is_admin and bot.workspaces(writable=True) == ()
    assert not person.is_service and person.is_admin


def test_a_declared_principal_kind_must_match_the_token() -> None:
    check_kind(User(id="bot", kind="service"), "service")
    check_kind(PROBE, None)

    with pytest.raises(HTTPException) as excinfo:
        check_kind(PROBE, "service")

    assert excinfo.value.status_code == 403
