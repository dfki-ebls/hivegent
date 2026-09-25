"""AI-generated text watermark detection, signed reports and export provenance."""

import json
import time
from functools import cache
from typing import Literal, cast

from joserfc import jws
from joserfc.jwk import OKPKey
from pydantic import BaseModel, Field, computed_field

from .config import content_digest, settings
from .http_client import get_trusted_http_client
from .keys import derived_ed25519_key
from .types import (
    ServerConversation,
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
    "sign_provenance",
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


def _issuer() -> str:
    return settings.transparency.report_issuer.rstrip("/")


def _now() -> int:
    return int(time.time())


class _SignedClaims(BaseModel):
    content_sha256: str
    iss: str = Field(default_factory=_issuer)
    iat: int = Field(default_factory=_now)

    @computed_field
    @property
    def sub(self) -> str:
        return f"sha256:{self.content_sha256}"


class _ReportClaims(_SignedClaims):
    # The detection solution is this endpoint, not the detector behind it:
    # naming the upstream URL would publish internal topology in a report
    # whose whole point is that anyone can re-run the check.
    detector: str = Field(default_factory=lambda: f"{_issuer()}/api/transparency/detect")
    method: Literal["watermark"] = "watermark"
    status: _DetectionStatus


class _ProvenanceClaims(_SignedClaims):
    ai_generated: Literal[True] = True


def get_transparency_config() -> TransparencyConfig:
    """Return the public subset of the deployment's transparency settings."""
    return TransparencyConfig(
        enabled=settings.transparency.enabled,
        contact_email=settings.transparency.contact_email or None,
        minimum_watermark_tokens=MINIMUM_WATERMARK_TOKENS,
    )


@cache
def _signing_key() -> OKPKey:
    """Return the report signing key, derived from the application secret.

    Nothing is stored: the key is stable for as long as the secret is, so a
    report downloaded today still verifies tomorrow.
    """
    return OKPKey.import_key(derived_ed25519_key(_SIGNING_PURPOSE))


def _jwk(key: OKPKey) -> TransparencyJwk:
    return TransparencyJwk(x=cast(str, key.as_dict(private=False)["x"]), kid=key.thumbprint())


@cache
def public_jwks() -> TransparencyJwks:
    """Return the keys that verify detection reports and export provenance.

    The active key comes first, followed by the retired keys that still verify
    what was signed before a rotation.
    """
    retired = (
        OKPKey.import_key({"kty": "OKP", "crv": "Ed25519", "x": x})
        for x in settings.transparency.retired_public_keys
    )

    return TransparencyJwks(keys=[_jwk(_signing_key()), *map(_jwk, retired)])


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


def _sign(claims: _SignedClaims) -> str:
    key = _signing_key()
    header = _ReportHeader(kid=key.thumbprint(), jku=f"{_issuer()}/api/transparency/jwks")

    return jws.serialize_compact(
        header.model_dump(),
        claims.model_dump_json().encode(),
        key,
        algorithms=["Ed25519"],
    )


def sign_provenance(conversation: ServerConversation) -> str | None:
    """Return a signed statement that *conversation* contains AI-generated text.

    Returns ``None`` while transparency is disabled, since nothing is signed
    then. The digest covers the conversation without its ``provenance`` field,
    serialized as JSON with sorted keys, no whitespace and unescaped unicode,
    so a verifier recomputes it from the exported file.
    """
    if not settings.transparency.enabled:
        return None

    canonical = json.dumps(
        conversation.model_dump(mode="json", exclude={"provenance"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return _sign(_ProvenanceClaims(content_sha256=content_digest(canonical)))


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
        signed_report=_sign(_ReportClaims(content_sha256=content_digest(text), status=status)),
    )
