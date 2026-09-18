"""AI-generated text watermark detection and signed verification reports."""

import hashlib
import time
from dataclasses import dataclass
from functools import cache
from typing import Literal, cast

from joserfc import jws
from joserfc.jwk import OKPKey
from pydantic import BaseModel, Field

from .config import settings
from .http_client import get_trusted_http_client
from .keys import derived_ed25519_key
from .types import (
    TransparencyConfig,
    TransparencyDetectionResponse,
    TransparencyJwk,
    TransparencyJwks,
)

__all__ = [
    "MINIMUM_WATERMARK_TOKENS",
    "TransparencyUnavailable",
    "detect_text",
    "get_transparency_config",
    "public_jwks",
]

#: Label separating the report signing key from every other derived key.
_SIGNING_PURPOSE = "transparency-report-signing"

_DetectionStatus = Literal["detected", "not_detected", "inconclusive"]

# Text shorter than this cannot be watermarked with even a basic level of
# reliability, so vLLM marks nothing below it and a negative result says
# nothing about the text. The threshold is the state of the art, not a
# constant of the scheme, and drops as detection improves.
MINIMUM_WATERMARK_TOKENS = 200


class TransparencyUnavailable(RuntimeError):
    """Raised when the configured watermark detector cannot answer safely."""


class _DetectorRequest(BaseModel):
    text: str


class _DetectorResponse(BaseModel):
    """vLLM's reference detector response.

    The field names are upstream's, so no adapter sits between the detector
    every other vLLM deployment runs and the one this reads.
    """

    score: float
    p_value: float = Field(ge=0, le=1)
    num_scored_tokens: int = Field(ge=0)
    is_watermarked: bool


class _ReportHeader(BaseModel):
    alg: Literal["Ed25519"] = "Ed25519"
    typ: Literal["JWT"] = "JWT"
    kid: str
    jku: str


class _ReportClaims(BaseModel):
    iss: str
    sub: str
    iat: int
    content_sha256: str
    detector: str
    method: Literal["watermark"] = "watermark"
    status: Literal["detected", "not_detected", "inconclusive"]


def get_transparency_config() -> TransparencyConfig:
    """Return the public subset of the deployment's transparency settings."""
    return TransparencyConfig(
        enabled=settings.transparency.enabled,
        contact_email=settings.transparency.contact_email or None,
        minimum_watermark_tokens=MINIMUM_WATERMARK_TOKENS,
    )


@dataclass(slots=True, frozen=True)
class _SigningKey:
    """The report signing key and its RFC 7638 thumbprint."""

    key: OKPKey
    kid: str


@cache
def _signing_key() -> _SigningKey:
    """Return the report signing key, derived from the application secret.

    Nothing is stored: the key is stable for as long as the secret is, so a
    report downloaded today still verifies tomorrow, and the derivation plus
    the thumbprint happen once per process instead of once per request.
    """
    key = OKPKey.import_key(derived_ed25519_key(_SIGNING_PURPOSE))

    return _SigningKey(key=key, kid=key.thumbprint())


def public_jwks() -> TransparencyJwks:
    """Return the public key used to verify signed detection reports."""
    signing = _signing_key()

    return TransparencyJwks(
        keys=[
            TransparencyJwk(
                x=cast(str, signing.key.as_dict(private=False)["x"]),
                kid=signing.kid,
            )
        ]
    )


def _status(result: _DetectorResponse) -> _DetectionStatus:
    if result.is_watermarked:
        return "detected"

    if result.num_scored_tokens < MINIMUM_WATERMARK_TOKENS:
        return "inconclusive"

    return "not_detected"


def _message(status: _DetectionStatus) -> str:
    if status == "detected":
        return "A Hivegent text watermark was detected."

    if status == "inconclusive":
        return "The text is too short for a reliable negative result."

    return (
        "No Hivegent text watermark was detected. This does not prove that the "
        "text was written by a person or by another AI system."
    )


def _signed_report(text: str, status: _DetectionStatus) -> str:
    signing = _signing_key()
    digest = hashlib.sha256(text.encode()).hexdigest()
    issuer = settings.transparency.report_issuer.rstrip("/")
    header = _ReportHeader(
        kid=signing.kid,
        jku=f"{issuer}/api/transparency/jwks",
    )
    # The detection solution is this endpoint, not the detector behind it:
    # naming the upstream URL would publish internal topology in a report
    # whose whole point is that anyone can re-run the check.
    claims = _ReportClaims(
        iss=issuer,
        sub=f"sha256:{digest}",
        iat=int(time.time()),
        content_sha256=digest,
        detector=f"{issuer}/api/transparency/detect",
        status=status,
    )

    return jws.serialize_compact(
        header.model_dump(),
        claims.model_dump_json().encode(),
        signing.key,
        algorithms=["Ed25519"],
    )


async def detect_text(text: str) -> TransparencyDetectionResponse:
    """Detect the configured watermark without retaining the submitted text."""
    api_key = settings.transparency.detector_api_key

    try:
        response = await get_trusted_http_client().post(
            settings.transparency.detector_url,
            json=_DetectorRequest(text=text).model_dump(),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
        )
        response.raise_for_status()
        result = _DetectorResponse.model_validate(response.json())
    except Exception as exc:
        raise TransparencyUnavailable("Watermark detector request failed") from exc

    status = _status(result)

    # The score and the p-value stay here: published, they are the feedback
    # signal an attacker needs to strip or forge the watermark.
    return TransparencyDetectionResponse(
        status=status,
        message=_message(status),
        signed_report=_signed_report(text, status),
    )
