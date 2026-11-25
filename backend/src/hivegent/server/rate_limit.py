"""Admission of every request, and per-user rate limits on the routes that spend a model or the pipeline.

Each limit is a token bucket per principal held in the memory of one server
process, so it is exact for one server, and several servers behind one address
each grant the full limit, a deliberate trade against a shared store.  Every authenticated request, to the
REST API and to ``/mcp`` alike, passes :func:`admit`: the maintenance gate and
the ``rate_limits.request`` bucket.  A costly route declares its own limit on
top with one of the dependencies here, or spends one of the limits itself where
only the request it parsed tells which.  The Teams bridge, the one service
principal, answers for many people at once, so it spends its own, larger
``rate_limits.teams`` buckets instead.
A request over its limit is refused with 429 and a ``Retry-After`` naming when
its next token is back.
"""

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from fastapi.params import Depends as DependsParam
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from ..auth import CurrentPrincipal, user_from_claims
from ..config import settings
from ..config.limits import RateLimit, UserRateLimits
from ..shared.l10n import Localized
from ..store import User
from .maintenance import check_maintenance
from .problems import handle_problem

__all__ = [
    "ADMISSION",
    "CHAT",
    "DETECTION_LIMIT",
    "GENERATION_LIMIT",
    "RESUME",
    "TRANSCRIPTION_LIMIT",
    "UPLOAD_LIMIT",
    "Limit",
    "McpAdmission",
    "TokenBuckets",
    "admit",
]


def _too_many(seconds: int) -> Localized[str]:
    return Localized(
        en=f"Too many requests, try again in {seconds} s.",
        de=f"Zu viele Anfragen, versuchen Sie es in {seconds} s erneut.",
    )


_SWEEP_SECONDS = 60.0
"""How often the buckets that refilled are forgotten."""


@dataclass(slots=True)
class TokenBuckets:
    """One token bucket per key, filled to ``burst`` at first sight.

    A bucket is the time it is full again, so one that refilled is no
    different from a new one and is forgotten by the next sweep.
    """

    _full_at: dict[str, float] = field(default_factory=dict)
    _sweep_at: float = 0.0

    def __len__(self) -> int:
        """How many buckets are kept."""
        return len(self._full_at)

    def take(self, key: str, limit: RateLimit, now: float) -> float:
        """Take a token of *key*'s bucket, returning ``0`` or the seconds until one is back."""
        if now >= self._sweep_at:
            self._full_at = {k: t for k, t in self._full_at.items() if t > now}
            self._sweep_at = now + _SWEEP_SECONDS

        rate = limit.per_minute / 60
        full_at = max(self._full_at.get(key, now), now)
        tokens = limit.burst - (full_at - now) * rate

        if tokens < 1:
            return (1 - tokens) / rate

        self._full_at[key] = full_at + 1 / rate

        return 0.0


@dataclass(slots=True)
class Limit:
    """The limit *select* picks, from the Teams bridge's limits for it and a person's for everyone else, spent from one bucket per principal."""

    select: Callable[[UserRateLimits], RateLimit]
    buckets: TokenBuckets = field(default_factory=TokenBuckets)

    def spend(self, user: User) -> None:
        """Spend one of *user*'s tokens.

        Raises:
            HTTPException: 429 with ``Retry-After`` while the bucket is empty.
        """
        limits = settings.rate_limits.teams if user.is_service else settings.rate_limits
        limit = self.select(limits)

        if wait := self.buckets.take(user.id, limit, time.monotonic()):
            seconds = math.ceil(wait)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                _too_many(seconds).current,
                headers={"Retry-After": str(seconds)},
            )

    def dependency(self) -> DependsParam:
        """The route dependency spending a token of the caller's."""

        async def spend(user: CurrentPrincipal) -> None:
            self.spend(user)

        return Depends(spend)


_REQUEST = Limit(lambda limits: limits.request)


def admit(user: User) -> None:
    """Admit *user*'s request, refused during maintenance and over the request limit.

    Raises:
        ProblemException: 503 during maintenance for a non-admin.
        HTTPException: 429 with ``Retry-After`` over the request limit.
    """
    check_maintenance(user)
    _REQUEST.spend(user)


async def _admit(user: CurrentPrincipal) -> None:
    admit(user)


ADMISSION: DependsParam = Depends(_admit)
"""The dependency admitting every request of the REST API."""

CHAT = Limit(lambda limits: limits.chat)
"""Spent by a chat turn, a regenerate, or a compaction, once the chat route parsed which it is."""
RESUME = Limit(lambda limits: limits.resume)
"""Spent instead of :data:`CHAT` by a request that answers the approvals or questions a turn awaits."""
UPLOAD_LIMIT = Limit(lambda limits: limits.upload).dependency()
TRANSCRIPTION_LIMIT = Limit(lambda limits: limits.transcription).dependency()
GENERATION_LIMIT = Limit(lambda limits: limits.generation).dependency()
DETECTION_LIMIT = Limit(lambda limits: limits.detection).dependency()


@dataclass(slots=True, frozen=True)
class McpAdmission:
    """ASGI middleware admitting every authenticated request to ``/mcp`` as :func:`admit` does.

    It runs behind FastMCP's authentication, so a request without a valid
    token passes on to be refused there, and *principal* names the caller of
    one that has, ``None`` to read it from the token.
    """

    app: ASGIApp
    principal: Callable[[], Awaitable[User]] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (user := await self._user(scope)) is not None:
            try:
                admit(user)
            except StarletteHTTPException as exc:
                response = await handle_problem(Request(scope), exc)
                await response(scope, receive, send)

                return

        await self.app(scope, receive, send)

    async def _user(self, scope: Scope) -> User | None:
        if self.principal is not None:
            return await self.principal()

        match scope.get("user"):
            case AuthenticatedUser(access_token=token) if token.claims:
                return user_from_claims(token.claims)
            case _:
                return None
