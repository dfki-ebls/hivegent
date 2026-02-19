"""OIDC authentication and JWT validation."""

import time
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from joserfc import jwt
from joserfc.errors import (
    ExpiredTokenError,
    InvalidClaimError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwk import KeySet
from joserfc.jwt import ClaimsOption, JWTClaimsRegistry
from pydantic_settings import BaseSettings, SettingsConfigDict

from .tokens import token_store
from .types import User

__all__ = [
    "AuthSettings",
    "User",
    "get_current_user",
]


class AuthSettings(BaseSettings):
    """Authentication settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SNIPSCOUT_AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    disabled: bool = False
    issuer: str = ""
    audience: str | None = None
    jwks_cache_ttl: int = 3600


auth_settings = AuthSettings()


class JWKSFetcher:
    """Fetch and cache JWKS from OIDC provider."""

    def __init__(self) -> None:
        self._cache: KeySet | None = None
        self._cache_time: float = 0
        self._client = httpx.AsyncClient()

    def _is_cache_valid(self) -> bool:
        """Check if the cached JWKS is still valid."""
        if self._cache is None:
            return False
        return (time.time() - self._cache_time) < auth_settings.jwks_cache_ttl

    async def get_jwks(self, force_refresh: bool = False) -> KeySet:
        """Fetch JWKS from the OIDC provider.

        Args:
            force_refresh: Force a refresh even if cache is valid.

        Returns:
            A KeySet containing the provider's public keys.

        Raises:
            HTTPException: If JWKS cannot be fetched.
        """
        if not force_refresh and self._is_cache_valid():
            assert self._cache is not None
            return self._cache

        if not auth_settings.issuer:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OIDC issuer not configured",
            )

        jwks_uri = f"{auth_settings.issuer.rstrip('/')}/.well-known/jwks.json"

        try:
            response = await self._client.get(jwks_uri, timeout=10.0)
            response.raise_for_status()
            jwks_data = response.json()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to fetch JWKS: {e}",
            ) from e

        try:
            self._cache = KeySet.import_key_set(jwks_data)
            self._cache_time = time.time()
            return self._cache
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid JWKS format: {e}",
            ) from e


_jwks_fetcher = JWKSFetcher()


def _build_claims_registry() -> JWTClaimsRegistry:
    """Build a JWT claims registry based on current auth settings.

    Returns:
        A configured JWTClaimsRegistry instance.
    """
    options: dict[str, ClaimsOption] = {"sub": ClaimsOption(essential=True)}
    if auth_settings.issuer:
        options["iss"] = ClaimsOption(value=auth_settings.issuer)
    if auth_settings.audience:
        options["aud"] = ClaimsOption(value=auth_settings.audience)
    return JWTClaimsRegistry(leeway=300, **options)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


async def validate_jwt_token(token: str) -> User:
    """Validate a JWT token and extract user information.

    Args:
        token: The JWT token to validate.

    Returns:
        A User instance with extracted claims.

    Raises:
        HTTPException: If the token is invalid.
    """
    key_set = await _jwks_fetcher.get_jwks()

    try:
        decoded = jwt.decode(token, key_set)
    except JoseError:
        # Try refreshing JWKS in case keys were rotated
        try:
            key_set = await _jwks_fetcher.get_jwks(force_refresh=True)
            decoded = jwt.decode(token, key_set)
        except JoseError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token signature: {e}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

    try:
        claims_registry = _build_claims_registry()
        claims_registry.validate(decoded.claims)
    except ExpiredTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except MissingClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token missing required claim: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except InvalidClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token claim: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    claims = decoded.claims
    return User(
        id=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name") or claims.get("preferred_username"),
    )


# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> User:
    """FastAPI dependency to get the current authenticated user.

    Validates the Bearer token from the Authorization header.
    Supports both JWT tokens and Personal Access Tokens (PATs).

    When SNIPSCOUT_AUTH_DISABLED=true, returns a dev user without validation.

    Args:
        credentials: The HTTP Bearer credentials.

    Returns:
        The authenticated User.

    Raises:
        HTTPException: If authentication fails.
    """
    # Bypass authentication in development mode
    if auth_settings.disabled:
        return User(
            id="localhost",
            email="dev@localhost",
            name="Localhost User",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Check if this is a Personal Access Token (starts with snipscout_)
    if token.startswith("snipscout_"):
        user = token_store.validate_token(token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid personal access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Otherwise validate as JWT
    return await validate_jwt_token(token)
