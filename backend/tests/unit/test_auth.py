"""Unit tests for token verification and claim parsing."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
from fastapi import HTTPException
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey
from pydantic import HttpUrl, SecretStr
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, TextDeltaChunk
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
from hivegent.runs import Prepared, Runs
from hivegent.security import OperatorUrl
from hivegent.store import User
from tests.conftest import Configure
from tests.helpers import owned

ISSUER = OperatorUrl("https://auth.example.com/oidc")
JWKS_URI = f"{ISSUER}/certs"
AUDIENCE = ["hivegent-api"]
KEY = RSAKey.generate_key(2048, parameters={"kid": "rsa"})
HMAC = OctKey.generate_key(parameters={"kid": "hmac"})
NOW = 1_800_000_000

type Discovery = dict[str, Any]


class FakeClock(auth.Clock):
    """Time that only the test moves, waking whoever waits for it."""

    def __init__(self) -> None:
        self.time = float(NOW)
        self._moved = asyncio.Event()

    def now(self) -> float:
        return self.time

    async def until(self, at: float) -> None:
        while self.time < at:
            _ = await self._moved.wait()

    def advance_to(self, time: float) -> None:
        self.time = time
        self._moved.set()
        self._moved = asyncio.Event()


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Every test of this file reads the time the test sets."""
    clock = FakeClock()
    monkeypatch.setattr(auth, "clock", clock)

    return clock


def _claims(**changes: Any) -> dict[str, Any]:
    claims = {
        "iss": ISSUER,
        "sub": "user-1",
        "aud": "hivegent-api",
        "azp": "hivegent-api",
        "typ": "Bearer",
        "iat": NOW,
        "exp": NOW + 300,
    }

    return {k: v for k, v in {**claims, **changes}.items() if v is not None}


def _token(claims: dict[str, Any], alg: str = "RS256") -> str:
    key = KEY if alg.startswith("RS") else HMAC
    algorithms = None if alg.startswith("RS") else [alg]

    return jwt.encode({"alg": alg, "kid": key.kid}, claims, key, algorithms=algorithms)


@dataclass(slots=True)
class Idp:
    """The issuer's discovery document and keys, served by a mock transport."""

    discovery: Discovery
    client: httpx2.AsyncClient
    fetcher: JWKSFetcher

    def fetcher_for(self, issuer: str) -> JWKSFetcher:
        """A fetcher configured with the *issuer*."""
        return JWKSFetcher(lambda: OperatorUrl(issuer), lambda _: self.client)

    async def verify(
        self, token: str, fetcher: JWKSFetcher | None = None
    ) -> auth.Credential:
        return await authenticate(token, AUDIENCE, fetcher or self.fetcher)


@pytest.fixture()
async def idp() -> AsyncIterator[Idp]:
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

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        idp = Idp(discovery, client, JWKSFetcher(lambda: ISSUER, lambda _: client))

        yield idp


@pytest.mark.parametrize("configured", [ISSUER, f"{ISSUER}/"])
async def test_a_token_of_the_discovered_issuer_names_its_principal(
    idp: Idp, configured: str
) -> None:
    """Both spellings of the configured issuer address one discovery document, whose issuer the tokens carry byte for byte."""
    fetcher = idp.fetcher_for(configured)

    assert (await idp.verify(_token(_claims()), fetcher)).user.id == "user-1"

    with pytest.raises(HTTPException, match="iss"):
        await idp.verify(_token(_claims(iss=f"{ISSUER}/")), fetcher)


async def test_a_verified_token_renews_the_leases_of_its_principal(
    idp: Idp,
) -> None:
    with auth.leases.hold(User("user-1", expires_at=0)) as lease:
        credential = await idp.verify(_token(_claims()))

    assert credential.user.expires_at is not None
    assert lease.current.expires_at == credential.user.expires_at > NOW


@pytest.mark.parametrize("alg", ["HS256", "none"])
async def test_only_asymmetric_algorithms_are_accepted(idp: Idp, alg: str) -> None:
    """Whatever discovery advertises, a token signed by a shared secret or unsigned is refused."""
    if alg == "none":
        header = jwt.encode({"alg": "RS256"}, _claims(), KEY).split(".")[1]
        token = f"eyJhbGciOiJub25lIn0.{header}."
    else:
        token = _token(_claims(), alg)

    with pytest.raises(HTTPException) as exc_info:
        await idp.verify(token)

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        _claims(exp=None),
        _claims(iat=None),
        _claims(iss="https://evil.example.com"),
        _claims(aud="hivegent-api-x"),
        _claims(exp=NOW - 3600),
        _claims(typ="ID"),
        _claims(typ=None),
        _claims(azp=None),
        _claims(client_id="hivegent-bot"),
    ],
)
async def test_a_token_lacking_a_required_claim_is_refused(
    idp: Idp, claims: dict[str, Any]
) -> None:
    """The 401 names at most the claim, never the expected or received value."""
    with pytest.raises(HTTPException) as exc_info:
        await idp.verify(_token(claims))

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
    idp: Idp, discovery: Discovery
) -> None:
    """Another issuer, keys off the issuer's https origin, or no asymmetric algorithm."""
    idp.discovery.update(discovery)

    with pytest.raises(HTTPException) as exc_info:
        await idp.verify(_token(_claims()))

    assert exc_info.value.status_code == 500


async def test_mcp_advertises_the_resource_its_tokens_must_name(
    configure: Configure,
) -> None:
    """The challenge and the metadata of ``/mcp`` name the resource, which is the audience its tokens carry."""
    configure(
        auth={"issuer": ISSUER},
        mcp={
            "base_url": HttpUrl("https://hivegent.test/mcp"),
            "mode": "remote",
            "client_id": None,
            "confirmation_key": SecretStr("k" * 32),
        },
    )
    app = Starlette(routes=build_mcp_app().routes)

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://hivegent.test"
    ) as client:
        challenge = (await client.post("/mcp")).headers["www-authenticate"]
        metadata_url = "https://hivegent.test/.well-known/oauth-protected-resource/mcp"
        assert f'resource_metadata="{metadata_url}"' in challenge
        resource = (await client.get(metadata_url)).json()["resource"]

    assert resource == "https://hivegent.test/mcp"


async def test_get_jwks_serves_stale_keys_until_their_maximum_age(
    clock: FakeClock,
) -> None:
    """A failed refresh falls back to the cached keys, retried after a pause, until they are too old to trust."""
    calls: list[str] = []

    def unavailable(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))

        return httpx2.Response(503)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(unavailable))
    fetcher = JWKSFetcher(lambda: ISSUER, lambda _: client)
    stale = auth._Keys(KeySet([KEY]), ["RS256"], ISSUER)
    fetcher._keys._value = stale
    fetcher._keys._fetched = clock.time - settings.auth.jwks_cache_ttl

    assert await fetcher.get() is stale
    assert await fetcher.get() is stale
    assert len(calls) == 1

    fetcher._keys._fetched = clock.time - settings.auth.jwks_max_age_seconds

    with pytest.raises(HTTPException):
        await fetcher.get()


async def test_admin_is_a_role_not_a_group(idp: Idp) -> None:
    """Admin derives from the roles claim, the same name in groups must not grant it."""
    user = (await idp.verify(_token(_claims(groups=["admin", "eng:write"])))).user
    assert not user.is_admin
    assert "admin" in user.all_groups
    assert "eng" in user.write_groups

    user = (await idp.verify(_token(_claims(roles=["admin"])))).user
    assert user.is_admin
    assert not user.all_groups


async def test_a_stream_ends_when_its_token_expires(clock: FakeClock) -> None:
    """Events flow until the expiry, then the stream ends without waiting for more."""

    async def events() -> AsyncIterator[int]:
        yield 1
        _ = await asyncio.Event().wait()
        yield 2

    stream = until_expiry(events(), NOW + 10)

    assert await anext(stream) == 1

    clock.advance_to(NOW + 10)

    with pytest.raises(StopAsyncIteration):
        _ = await anext(stream)


async def test_a_lease_is_renewed_only_by_a_token_holding_its_grants() -> None:
    eng = frozenset({"eng"})

    with auth.leases.hold(User("a", read_groups=eng, expires_at=NOW + 10)) as lease:
        auth.leases.renew(User("a", expires_at=NOW + 3600))
        assert lease.current.expires_at == NOW + 10

        auth.leases.renew(User("a", read_groups=eng, expires_at=NOW + 3600))
        assert lease.current.expires_at == NOW + 3600


async def test_a_turn_stops_once_no_request_renews_its_authorization(
    clock: FakeClock,
) -> None:
    """The lease outlives its token by the renewal window, and a narrower token leaves it to lapse."""
    alice = User("alice", read_groups=frozenset({"eng"}), expires_at=NOW + 10)
    conversation = owned("c1", alice)

    async def turn() -> AsyncIterator[BaseChunk]:
        yield chunk
        _ = await asyncio.Event().wait()

    chunk = TextDeltaChunk(id="t", delta="a")
    run = Runs().start(conversation, alice, None, _prepared(turn()))
    assert await anext(run.follow(alice)) == chunk
    auth.leases.renew(User("alice", expires_at=NOW + 3600))

    clock.advance_to(NOW + 10 + auth._RENEWAL_SECONDS)
    await run.wait()

    assert run.chunks == [chunk]


async def _prepared(stream: AsyncIterator[BaseChunk]) -> Prepared:
    return Prepared(stream, [])


def test_claim_paths_walk_nested_mappings_and_union() -> None:
    """A path descends through nested mappings, and the entries of every path are unioned."""
    claims = {"groups": ["eng:write"], "custom": {"groups": ["team-kb:read"]}}
    assert auth._resolve_claim_path(claims, ["groups", "nope"]) is None

    def parsed(paths: list[list[str]]) -> dict[str, str | None]:
        entries = auth._claim_entries(claims, paths)
        return {c.id: c.permission for c in parse_group_claims(entries)}

    assert parsed([["groups"]]) == {"eng": "write"}
    assert parsed([["groups"], ["custom", "groups"]]) == {
        "eng": "write",
        "team-kb": "read",
    }


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (
            ["eng:write", "sales:read", "ops"],
            [
                GroupClaim("eng", permission="write"),
                GroupClaim("sales", permission="read"),
                GroupClaim("ops"),
            ],
        ),
        # RFC 7643 SCIM objects keep their ID verbatim, so a ``:`` in it survives.
        (
            [{"value": "9uh:8ei", "display": "Engineering"}],
            [GroupClaim("9uh:8ei", "Engineering")],
        ),
        ([":write", "", 42, {"display": "x"}], []),
    ],
    ids=["strings", "scim", "malformed"],
)
def test_group_entries_parse_to_claims_and_malformed_ones_are_skipped(
    entries: list[Any], expected: list[GroupClaim]
) -> None:
    assert list(parse_group_claims(entries)) == expected


def test_the_service_client_is_a_service_whatever_it_claims() -> None:
    """Its groups and roles are never read, so it sees the global workspace alone."""
    claims = {"sub": "bot", "groups": ["team:write"], "roles": ["admin"]}

    bot = user_from_claims(
        {**claims, "azp": "hivegent-bot"}, service_client_id="hivegent-bot"
    )
    person = user_from_claims(
        {**claims, "azp": "hivegent-spa"}, service_client_id="hivegent-bot"
    )

    assert bot.is_service and not bot.is_admin and bot.workspaces(writable=True) == ()
    assert not person.is_service and person.is_admin


def test_a_declared_principal_kind_must_match_the_token() -> None:
    check_kind(User(id="bot", kind="service"), "service")
    check_kind(PROBE, None)

    with pytest.raises(HTTPException) as excinfo:
        check_kind(PROBE, "service")

    assert excinfo.value.status_code == 403
