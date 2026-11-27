"""Tests for the per-user token buckets."""

from collections.abc import Callable

import pytest
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from hivegent.config.limits import (
    RateLimit,
    RateLimitSettings,
    TeamsRateLimitSettings,
    UserRateLimits,
)
from hivegent.server.maintenance import MAINTENANCE_PROBLEM
from hivegent.server.problems import ProblemException
from hivegent.server.rate_limit import Limit, McpAdmission, TokenBuckets
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


def _limit(
    select: Callable[[UserRateLimits], RateLimit], limits: RateLimitSettings
) -> Limit:
    """A limit over *limits* whose clock stands still."""
    return Limit(select, limits=lambda: limits, clock=lambda: 0.0)


def test_a_service_spends_its_own_limit() -> None:
    """The bridge's one identity is bounded by ``rate_limits.teams``, not a person's chat limit."""
    chat = _limit(
        lambda limits: limits.chat,
        RateLimitSettings(
            chat=RateLimit(burst=1, per_minute=1),
            teams=TeamsRateLimitSettings(chat=RateLimit(burst=2, per_minute=1)),
        ),
    )
    bot = User("bot", kind="service")

    chat.spend(bot)
    chat.spend(bot)

    with pytest.raises(HTTPException):
        chat.spend(bot)


def test_an_empty_bucket_answers_429_with_the_seconds_until_a_token() -> None:
    """The ``Retry-After`` names when the next token is back."""
    request = _limit(
        lambda limits: limits.request,
        RateLimitSettings(request=RateLimit(burst=1, per_minute=1)),
    )
    user = User("u")
    request.spend(user)

    with pytest.raises(HTTPException) as refused:
        request.spend(user)

    assert refused.value.status_code == 429
    assert refused.value.headers == {"Retry-After": "60"}


def test_mcp_requests_are_refused_as_the_api_refuses_them() -> None:
    """``/mcp`` answers a refusal of its admission with the problem or status it carries."""
    refusals: list[StarletteHTTPException] = [
        ProblemException(503, "closed", type=MAINTENANCE_PROBLEM, title="Maintenance"),
        HTTPException(429, "slow down", headers={"Retry-After": "60"}),
    ]

    def admission(_user: User) -> None:
        if refusals:
            raise refusals.pop(0)

    async def principal() -> User:
        return User("u")

    app = Starlette(
        routes=[Route("/", lambda _: PlainTextResponse("ok"))],
        middleware=[Middleware(McpAdmission, principal=principal, admission=admission)],
    )
    client = TestClient(app)

    assert client.get("/").status_code == 503
    refused = client.get("/")
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert client.get("/").status_code == 200
