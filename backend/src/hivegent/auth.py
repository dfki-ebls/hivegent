"""OIDC authentication and JWT validation."""

import asyncio
import logging
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

import httpx2
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from joserfc import jwt
from joserfc.errors import (
    DecodeError,
    ExpiredTokenError,
    InvalidClaimError,
    InvalidKeyIdError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwk import KeySet
from joserfc.jws import extract_compact
from joserfc.jwt import ClaimsOption, JWTClaimsRegistry
from pydantic import ValidationError

from .config import ADMIN_ROLE, sanitize_group_id, sanitize_user_id, settings
from .db.groups import list_group_ids
from .http_client import get_trusted_http_client
from .security import OperatorUrl, UnsafeUrlError, origin
from .shared.l10n import Localized
from .store import AccessDenied, User

__all__ = [
    "ASYMMETRIC_ALGORITHMS",
    "NOBODY",
    "PROBE",
    "Credential",
    "CurrentPrincipal",
    "CurrentUser",
    "GroupClaim",
    "TokenExpiry",
    "authenticate",
    "build_discovery_url",
    "check_kind",
    "dev_user",
    "fetch_oidc_configuration",
    "get_current_principal",
    "get_current_user",
    "parse_group_claims",
    "refuse_expired",
    "require_admin",
    "token_expiry",
    "until_expiry",
    "user_from_claims",
]

logger = logging.getLogger(__name__)

_INVALID_SIGNATURE = Localized(
    en="Invalid token signature", de="Ungültige Token-Signatur"
)
_TOKEN_EXPIRED = Localized(en="Token has expired", de="Das Token ist abgelaufen")


def _missing_claim(claim: str) -> Localized[str]:
    return Localized(
        en=f"Token missing required claim: {claim!r}",
        de=f"Im Token fehlt der erforderliche Claim „{claim}“",
    )


def _invalid_claim(claim: str) -> Localized[str]:
    return Localized(
        en=f"Invalid token claim: {claim!r}", de=f"Ungültiger Token-Claim „{claim}“"
    )


_INVALID_SUBJECT = Localized(
    en="Token subject is not a valid user identifier",
    de="Das Subjekt des Tokens ist keine gültige Benutzerkennung",
)
_NOT_AUTHENTICATED = Localized(en="Not authenticated", de="Nicht authentifiziert")

ASYMMETRIC_ALGORITHMS: frozenset[str] = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
        "Ed25519",
    }
)
"""Every signing algorithm a token may use, intersected with what discovery advertises.

Only asymmetric ones, so neither ``none`` nor a shared HMAC secret is ever
accepted, whatever the provider advertises.  RFC 9864 replaces the generic
``EdDSA`` with ``Ed25519``, but IdPs still sign with the former.
"""

_LEEWAY_SECONDS = 300


def build_discovery_url(issuer: str) -> str:
    """Return the OIDC Discovery 1.0 URL for ``issuer``.

    >>> build_discovery_url("https://idp.example/auth/v1/")
    'https://idp.example/auth/v1/.well-known/openid-configuration'
    """
    url = httpx2.URL(issuer)
    path = PurePosixPath(url.path) / ".well-known" / "openid-configuration"

    return str(url.copy_with(path=str(path)))


_MISCONFIGURED = Localized(
    en="Authentication is misconfigured on the server",
    de="Die Anmeldung ist auf dem Server falsch konfiguriert",
)

_IDP_UNAVAILABLE = Localized(
    en="The identity provider is unreachable, please try again later",
    de="Der Identitätsanbieter ist nicht erreichbar, bitte später erneut versuchen",
)


def _misconfigured(detail: str) -> HTTPException:
    """A 500 whose *detail* only the log gets, since any caller with a bearer string can trigger it."""
    logger.error("OIDC discovery refused: %s", detail)

    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, _MISCONFIGURED.current)


def _unavailable(what: str, error: Exception) -> HTTPException:
    """A 503 for a failed fetch of *what*, whose error only the log gets."""
    logger.warning("Fetching the %s failed: %s", what, error)

    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _IDP_UNAVAILABLE.current)


def _check_discovery(issuer: OperatorUrl, config: OIDCConfiguration) -> None:
    """Refuse a discovery document of another issuer, or keys served off its origin.

    A configured issuer may differ from the document's only by a trailing slash,
    since both address the same discovery document, and tokens are then checked
    against the document's exact spelling, the one they carry.

    Raises:
        HTTPException: 500 naming what is wrong, which only the operator can fix.
    """
    if str(config.issuer).rstrip("/") != issuer.rstrip("/"):
        raise _misconfigured("The discovery document names another issuer")

    jwks_uri = str(config.jwks_uri or "")

    try:
        jwks_origin = origin(jwks_uri)
    except UnsafeUrlError as exc:
        raise _misconfigured(f"Invalid jwks_uri: {exc}") from exc

    # The scheme is part of the origin, so the keys of an https issuer come
    # over https.
    if jwks_origin != origin(issuer):
        raise _misconfigured("jwks_uri is not on the issuer's origin")


async def fetch_oidc_configuration(issuer: OperatorUrl) -> OIDCConfiguration:
    """Fetch the discovery document of *issuer*, checked to be its own.

    Raises:
        HTTPException: 503 on transport failure, 500 on a malformed or
            foreign document.
    """
    try:
        response = await get_trusted_http_client(issuer).get(
            build_discovery_url(issuer), timeout=settings.auth.jwks_timeout_seconds
        )
        response.raise_for_status()
        config = OIDCConfiguration.model_validate({**response.json(), "strict": False})
    except httpx2.HTTPError as e:
        raise _unavailable("OIDC discovery document", e) from e
    except ValidationError as e:
        raise _misconfigured(f"Invalid OIDC discovery document: {e}") from e

    _check_discovery(issuer, config)

    return config


class _SingleFlightCache[T]:
    """TTL cache whose refreshes are single-flighted with stale fallback.

    Concurrent cache misses share one refresh, and a failed refresh
    serves the previously cached value instead of failing the caller,
    re-stamping the TTL so the next retry is a full interval away.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._value: T | None = None
        self._time: float = 0
        self._lock = asyncio.Lock()

    def _fresh(self, *, force_refresh: bool) -> T | None:
        if self._value is None:
            return None
        ttl = (
            settings.auth.jwks_force_refresh_min_interval_seconds
            if force_refresh
            else settings.auth.jwks_cache_ttl
        )
        return self._value if time.time() - self._time < ttl else None

    async def get(
        self, refresh: Callable[[], Awaitable[T]], *, force_refresh: bool
    ) -> T:
        """Return the cached value, refreshing it through ``refresh`` on expiry.

        Raises:
            Exception: Whatever ``refresh`` raised, only when no cached
                value is available to fall back to.
        """
        if (fresh := self._fresh(force_refresh=force_refresh)) is not None:
            return fresh
        async with self._lock:
            if (fresh := self._fresh(force_refresh=force_refresh)) is not None:
                return fresh
            try:
                value = await refresh()
            except Exception as e:
                if self._value is None:
                    raise
                logger.warning(
                    "%s refresh failed, serving cached copy: %s", self._name, e
                )
                value = self._value
            self._value = value
            self._time = time.time()
            return value


@dataclass(slots=True, frozen=True)
class _Keys:
    """The issuer's key set and the algorithms its tokens may be signed with."""

    key_set: KeySet
    algorithms: list[str]
    issuer: str
    """The discovery document's exact issuer, which every token's ``iss`` must equal."""


def _algorithms(config: OIDCConfiguration) -> list[str]:
    """The asymmetric algorithms discovery advertises, all of them when it names none.

    Raises:
        HTTPException: 500 when it advertises none of them.
    """
    advertised = config.id_token_signing_alg_values_supported

    if not advertised:
        return sorted(ASYMMETRIC_ALGORITHMS)

    if not (allowed := sorted(ASYMMETRIC_ALGORITHMS.intersection(advertised))):
        raise _misconfigured("The issuer advertises no asymmetric signing algorithm")

    return allowed


class JWKSFetcher:
    """Fetch and cache the issuer's keys via discovery.

    Concurrent cache misses share one request, and a transient IdP hiccup
    serves the cached keys instead of failing authenticated requests.
    Key-rotation safety is preserved by the kid-miss force-refresh path in
    :func:`authenticate`.
    """

    def __init__(self) -> None:
        self._keys = _SingleFlightCache[_Keys]("JWKS")

    @staticmethod
    async def _fetch() -> _Keys:
        """Fetch discovery, then the key set from its ``jwks_uri``.

        Raises:
            HTTPException: 503 on transport failure, 500 on a malformed or
                foreign document or key set.
        """
        issuer = settings.auth.issuer

        if issuer is None:
            raise _misconfigured("OIDC issuer not configured")

        config = await fetch_oidc_configuration(issuer)

        try:
            response = await get_trusted_http_client(issuer).get(
                str(config.jwks_uri), timeout=settings.auth.jwks_timeout_seconds
            )
            response.raise_for_status()
            jwks_data = response.json()
        except httpx2.HTTPError as e:
            raise _unavailable("JWKS", e) from e

        try:
            key_set = KeySet.import_key_set(jwks_data)
        except Exception as e:
            raise _misconfigured(f"Invalid JWKS format: {e}") from e

        return _Keys(key_set, _algorithms(config), str(config.issuer))

    async def get(self, force_refresh: bool = False) -> _Keys:
        """The issuer's keys, refetched past their TTL.

        Raises:
            HTTPException: If they cannot be fetched and none are cached.
        """
        return await self._keys.get(self._fetch, force_refresh=force_refresh)


_jwks_fetcher = JWKSFetcher()


def _should_refresh_jwks(token: str, key_set: KeySet) -> bool:
    """Refresh JWKS only when the token names a key id absent from the cache.

    Reading the header is safe because the signature isn't trusted yet: a
    mismatched ``kid`` is the only signal that key rotation, rather than
    tampering, caused the verification failure.
    """
    try:
        header = extract_compact(token.encode("ascii")).headers()
    except DecodeError, UnicodeEncodeError:
        return False
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return False
    try:
        key_set.get_by_kid(kid)
    except InvalidKeyIdError:
        return True
    return False


def _claims_registry(issuer: str, audience: Collection[str]) -> JWTClaimsRegistry:
    """The claims every token must carry, ``iss`` equal to *issuer* byte for byte."""
    return JWTClaimsRegistry(
        leeway=_LEEWAY_SECONDS,
        iss=ClaimsOption(essential=True, value=issuer),
        sub=ClaimsOption(essential=True),
        exp=ClaimsOption(essential=True),
        iat=ClaimsOption(essential=True),
        aud=ClaimsOption(essential=True, values=list(audience)),
    )


def _resolve_claim_path(claims: Mapping[str, Any], path: Sequence[str]) -> Any:
    """Resolve the claim at *path*, the keys leading to it through nested mappings.

    Returns ``None`` if any key is missing or a non-mapping is met mid-path.
    """
    value: Any = claims
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _claim_entries(
    claims: Mapping[str, Any], paths: Iterable[Sequence[str]]
) -> Iterator[Any]:
    """Yield the entries of every list-valued claim at the given paths.

    Reading several paths lets a single backend serve identities whose
    groups live at different claim locations, e.g. interactive users with
    a top-level ``groups`` claim alongside a service bot whose IdP can only
    nest static claims under ``custom``.  Entries from every path are
    yielded in order, non-list claims are skipped.
    """
    for path in paths:
        value = _resolve_claim_path(claims, path)
        if isinstance(value, list):
            yield from value


def _text(value: Any) -> str | None:
    """Return *value* if it is a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None


@dataclass(slots=True, frozen=True)
class GroupClaim:
    """One usable entry of a groups claim.

    ``id`` identifies the group everywhere; ``display`` is the label a
    client may show instead, and is ``None`` when the entry carried none.
    ``permission`` is the ``:read`` / ``:write`` suffix a bare string may
    carry, and ``None`` when the entry states none.
    """

    id: str
    display: str | None = None
    permission: str | None = None


def parse_group_claims(entries: Iterable[Any]) -> Iterator[GroupClaim]:
    """Yield one :class:`GroupClaim` per usable entry of a groups claim.

    OpenID Connect standardises no groups claim, so providers differ and
    both shapes in use are accepted:

    - The SCIM object of RFC 7643 §4.1.2, which RFC 9068 §2.2.3.1 names for
      this claim: ``{"value": <id>, "display": <name>}``.  The ID is taken
      verbatim, so one containing ``:`` survives, and it carries no
      permission since RFC 7643 models none.  Entra ID emits bare IDs, and
      Keycloak, Authentik, Okta and Auth0 can be pointed at the group ID
      through a claim mapper, all of which land here too.
    - A bare name, which is all some providers (Rauthy among them) can
      emit, optionally suffixed with the permission (``"engineering:write"``).
      There is no ID to recover, so the name doubles as one, which is why
      renaming a group on such a provider strands its workspace.

    Malformed entries (neither object nor string, empties, suffix-only) are
    skipped.
    """
    for entry in entries:
        if isinstance(entry, Mapping):
            if group_id := _text(entry.get("value")):
                yield GroupClaim(group_id, _text(entry.get("display")))

        elif token := _text(entry):
            group_id, separator, permission = token.rpartition(":")
            if not separator:
                yield GroupClaim(token)
            elif group_id:
                yield GroupClaim(group_id, permission=permission)


def _accessible_group_claims(claims: Mapping[str, Any]) -> Iterator[GroupClaim]:
    """Yield the group claims whose ID can name a workspace.

    An ID that could not is dropped with a warning, costing access to that
    group alone rather than the rest.
    """
    for claim in parse_group_claims(_claim_entries(claims, settings.claims.groups)):
        try:
            sanitize_group_id(claim.id)
        except ValueError as exc:
            logger.warning("skipping unusable group claim entry: %s", exc)
            continue

        yield claim


def _extract_groups(
    claims: Mapping[str, Any],
) -> tuple[frozenset[str], frozenset[str], dict[str, str]]:
    """Split the groups claim into ``(read_ids, write_ids, labels)``.

    A group is identified by its ID everywhere (path prefix, workspace
    directory, workspace key), so *labels* is display only: it maps the IDs
    a provider also supplied a name for, for the client to show in place of
    the ID.  Entries without an explicit permission use the
    ``default_group_permission`` setting.
    """
    default_write = settings.claims.default_group_permission == "write"
    read_ids: set[str] = set()
    write_ids: set[str] = set()
    labels: dict[str, str] = {}

    for claim in _accessible_group_claims(claims):
        if claim.permission == "write" or (claim.permission is None and default_write):
            write_ids.add(claim.id)
        else:
            read_ids.add(claim.id)

        if claim.display:
            labels[claim.id] = claim.display

    # A group named twice keeps the wider permission.
    return frozenset(read_ids - write_ids), frozenset(write_ids), labels


def _extract_roles(claims: Mapping[str, Any]) -> frozenset[str]:
    """Return the role names from the OIDC roles claim.

    Roles are global capabilities (e.g. admin), kept separate from the
    groups claim that models shared knowledge.  Non-string and empty
    entries are skipped.
    """
    return frozenset(
        text
        for entry in _claim_entries(claims, settings.claims.roles)
        if (text := _text(entry))
    )


def _rejected(reason: str, detail: Localized[str]) -> HTTPException:
    """A 401 for a token refused for *reason*, logged at debug level since it runs before any rate limit, while the client sees only *detail*."""
    logger.debug("Token rejected: %s", reason)

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail.current,
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(slots=True, frozen=True)
class Credential:
    """A verified token: the principal it names, its claims, and when it expires.

    It expires once :func:`authenticate` would refuse it, its ``exp`` plus the
    leeway, so a stream it opens lasts exactly as long as it is accepted.
    """

    user: User
    claims: Mapping[str, Any]
    expires_at: float


async def authenticate(token: str, audience: Collection[str]) -> Credential:
    """Verify *token* for one of *audience* and name its principal.

    The one verification the REST API, its streams, and ``/mcp`` share: an
    asymmetric signature by the issuer's current keys, the discovery
    document's exact issuer, one of *audience*, and a subject, issue, and expiry time.

    Raises:
        HTTPException: 401 for a refused token, logged with its reason, and
            500 or 503 when the issuer's keys cannot be had.
    """
    keys = await _jwks_fetcher.get()

    try:
        decoded = jwt.decode(token, keys.key_set, algorithms=keys.algorithms)
    except JoseError as e:
        if not _should_refresh_jwks(token, keys.key_set):
            raise _rejected(f"signature: {e}", _INVALID_SIGNATURE) from e

        keys = await _jwks_fetcher.get(force_refresh=True)

        try:
            decoded = jwt.decode(token, keys.key_set, algorithms=keys.algorithms)
        except JoseError as e:
            raise _rejected(f"signature: {e}", _INVALID_SIGNATURE) from e

    try:
        _claims_registry(keys.issuer, audience).validate(decoded.claims)
    except ExpiredTokenError as e:
        raise _rejected("expired", _TOKEN_EXPIRED) from e
    except MissingClaimError as e:
        raise _rejected(f"missing claim {e.claim!r}", _missing_claim(e.claim)) from e
    except InvalidClaimError as e:
        # Only the claim's name reaches the client, so a crafted token
        # cannot probe the configured issuer or audience.
        raise _rejected(f"invalid claim {e.claim!r}", _invalid_claim(e.claim)) from e

    try:
        user = user_from_claims(decoded.claims)
    except ValueError as e:
        raise _rejected("invalid subject", _INVALID_SUBJECT) from e

    return Credential(
        user, decoded.claims, float(decoded.claims["exp"]) + _LEEWAY_SECONDS
    )


def _is_service(claims: Mapping[str, Any]) -> bool:
    """Whether *claims* were issued to the configured service client.

    OpenID Connect names the client in ``azp``, RFC 9068 in ``client_id``.
    """
    client = settings.auth.service_client_id

    return client is not None and client in (claims.get("azp"), claims.get("client_id"))


def user_from_claims(claims: Mapping[str, Any]) -> User:
    """Build the :class:`User` that validated token *claims* describe.

    The one place claims become an identity, for the HTTP API and the MCP
    endpoint alike, so both grant the same groups with the same permissions.
    A token of the service client becomes a service principal, whose groups
    and roles are never read, so its capabilities follow from its kind alone.

    Raises:
        ValueError: If the subject is no valid user ID.
    """
    sub = claims.get("sub")
    user_id = sanitize_user_id(sub if isinstance(sub, str) else "")

    if _is_service(claims):
        return User(id=user_id, name=claims.get("name"), kind="service")

    read_groups, write_groups, group_labels = _extract_groups(claims)

    return User(
        id=user_id,
        email=claims.get("email"),
        name=claims.get("name") or claims.get("preferred_username"),
        read_groups=read_groups,
        write_groups=write_groups,
        group_labels=group_labels,
        roles=_extract_roles(claims),
    )


async def dev_user() -> User:
    """The identity every caller gets from ``hivegent dev``, which validates no token.

    It writes every group registered in the database and holds the admin
    role, so destructive endpoints are reachable through the same
    property-derived ``is_admin`` check used in prod.
    """
    return User(
        id="localhost",
        email="dev@localhost",
        name="Localhost User",
        write_groups=await list_group_ids(),
        roles=frozenset({ADMIN_ROLE}),
    )


# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


def _unexpected_principal(expected: str) -> Localized[str]:
    return Localized(
        en=f"This request is only accepted from a {expected} principal",
        de=f"Diese Anfrage wird nur von einem Prinzipal der Art „{expected}“ angenommen",
    )


EXPECTED_PRINCIPAL_HEADER = "X-Hivegent-Principal"
"""The header with which a client declares the kind of principal its token must be."""

type PrincipalKind = Literal["person", "service"]


def check_kind(principal: User, expected: PrincipalKind | None) -> None:
    """Refuse a principal of another kind than its client declared, if it declared one.

    Raises:
        HTTPException: 403 when the principal's kind differs from the declared one.
    """
    if expected is not None and expected != principal.kind:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, _unexpected_principal(expected).current
        )


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    expected: Annotated[
        PrincipalKind | None, Header(alias=EXPECTED_PRINCIPAL_HEADER)
    ] = None,
) -> User:
    """FastAPI dependency to get the current authenticated principal, a service too.

    Verifies the bearer token for ``auth.audience`` with :func:`authenticate`,
    and notes its expiry on the request for :func:`token_expiry`.  A client
    may declare the kind its token must authenticate as, as the Teams bridge
    declares a service on every request, so a token that turned into a
    person's never acts with that person's tools and data.
    ``hivegent dev`` overrides it with :func:`dev_user`.

    Raises:
        HTTPException: If authentication fails, or the principal is of another kind than declared.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_NOT_AUTHENTICATED.current,
            headers={"WWW-Authenticate": "Bearer"},
        )

    credential = await authenticate(credentials.credentials, settings.auth.audience)
    request.state.token_expires_at = credential.expires_at
    check_kind(credential.user, expected)

    return credential.user


def token_expiry(request: Request) -> float | None:
    """When the request's token expires, as a UNIX time, ``None`` without a token.

    A stream ends then, so its client reconnects with a fresh token and the
    grants it holds are read again.
    """
    return getattr(request.state, "token_expires_at", None)


TokenExpiry = Annotated[float | None, Depends(token_expiry)]
"""When the request's token expires, for a route that outlives it."""


def refuse_expired(expires_at: float | None) -> None:
    """Refuse a response the token expiring at *expires_at* no longer authorizes, as one that waited for a turn.

    Raises:
        HTTPException: 401 once the token expired.
    """
    if expires_at is not None and time.time() >= expires_at:
        raise _rejected("expired while answering", _TOKEN_EXPIRED)


async def until_expiry[T](
    events: AsyncIterator[T], expires_at: float | None
) -> AsyncIterator[T]:
    """The *events* until the token expiring at *expires_at* does, all of them for ``None``.

    A stream is authorized once, when it opens, so it must not outlive the
    token that authorized it: its client reconnects with a fresh one, whose
    grants are read anew.
    """
    while True:
        try:
            if expires_at is None:
                event = await anext(events)
            else:
                event = await asyncio.wait_for(anext(events), expires_at - time.time())
        except StopAsyncIteration, TimeoutError:
            return

        yield event


type CurrentPrincipal = Annotated[User, Depends(get_current_principal)]
"""The authenticated principal, a service too, only for the routes a service may reach."""


async def get_current_user(principal: CurrentPrincipal) -> User:
    """FastAPI dependency to get the current authenticated person.

    Every route takes it but the chat route, so a service principal reaches
    nothing else, whatever routes are added later.

    Raises:
        HTTPException: 403 for a service principal.
    """
    try:
        principal.require_person()
    except AccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    return principal


NOBODY = User(id="")
"""A principal holding no grant, for probing what code does without a caller."""

PROBE = User(id="probe")
"""A principal the tools of a run are built for to list them, which never acts.

Unlike :data:`NOBODY` it has a personal scope to resolve paths in, since
building a tool resolves the roots it reaches.
"""

type CurrentUser = Annotated[User, Depends(get_current_user)]
"""The authenticated person, the principal every route but the chat route acts as."""


async def require_admin(user: CurrentUser) -> User:
    """FastAPI dependency that gates a route on administrator privileges.

    Admin status is the ``User.is_admin`` property: the fixed ``admin``
    role present in the request's ``roles`` claim.  Non-admins receive 403.
    """
    user.require_admin()

    return user
