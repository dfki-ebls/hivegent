"""Tests for the per-user token buckets."""

import pytest
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from hivegent.config import settings
from hivegent.config.limits import RateLimit, RateLimitSettings, TeamsRateLimitSettings
from hivegent.server import maintenance, rate_limit
from hivegent.server.rate_limit import CHAT, McpAdmission, TokenBuckets
from hivegent.store import User


def test_bucket_refuses_past_its_burst_and_refills_per_user() -> None:
    """A user spends the burst, waits for a refill, and never drains another's bucket, and refilled buckets are forgotten."""
    buckets = TokenBuckets()
    limit = RateLimit(burst=2, per_minute=60)

    assert buckets.take("a", limit, 0.0) == 0
    assert buckets.take("a", limit, 0.0) == 0
    assert buckets.take("a", limit, 0.0) == 1.0
    assert buckets.take("b", limit, 0.0) == 0
    assert buckets.take("a", limit, 1.0) == 0
    assert len(buckets) == 2

    assert buckets.take("a", limit, 60.0) == 0
    assert len(buckets) == 1


def test_a_service_spends_its_own_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bridge's one identity is bounded by ``rate_limits.teams``, not a person's chat limit."""
    monkeypatch.setattr(
        settings,
        "rate_limits",
        RateLimitSettings(
            chat=RateLimit(burst=1, per_minute=1),
            teams=TeamsRateLimitSettings(chat=RateLimit(burst=2, per_minute=1)),
        ),
    )
    bot = User("bot", kind="service")

    CHAT.spend(bot)
    CHAT.spend(bot)

    with pytest.raises(HTTPException):
        CHAT.spend(bot)


def test_mcp_requests_are_admitted_like_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/mcp`` answers maintenance with 503 and an empty request bucket with 429 and ``Retry-After``."""
    monkeypatch.setattr(maintenance, "_flag", maintenance._Flag())
    monkeypatch.setattr(
        settings,
        "rate_limits",
        RateLimitSettings(request=RateLimit(burst=1, per_minute=1)),
    )
    monkeypatch.setattr(
        rate_limit, "_REQUEST", rate_limit.Limit(lambda limits: limits.request)
    )

    async def principal() -> User:
        return User("u")

    app = Starlette(
        routes=[Route("/", lambda _: PlainTextResponse("ok"))],
        middleware=[Middleware(McpAdmission, principal=principal)],
    )
    client = TestClient(app)

    maintenance.set_enabled(True)
    assert client.get("/").status_code == 503

    maintenance.set_enabled(False)
    assert client.get("/").status_code == 200

    refused = client.get("/")
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
