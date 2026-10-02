"""AI-generated text watermark detection, signed reports and export provenance."""

import json
import time
from functools import cache
from typing import Literal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from joserfc import jws
from joserfc.jwk import OKPKey
from pydantic import BaseModel, Field

from .config import TransparencySettings, content_digest, reveal, settings
from .http_client import get_trusted_http_client
from .types import (
    ServerConversation,
    TransparencyConfig,
    TransparencyDetectionResponse,
    TransparencyJwk,
    TransparencyJwks,
)

__all__ = [
    "MINIMUM_WATERMARK_TOKENS",
    "PROVENANCE_TYPE",
    "TransparencyUnavailable",
    "detect_text",
    "get_transparency_config",
    "public_jwks",
    "sign_provenance",
]

_ALG = "Ed25519"

#: The JWS ``typ`` of export provenance, so it is never mistaken for a report.
PROVENANCE_TYPE = "ai-provenance+jwt"

_DetectionStatus = Literal["detected", "not_detected", "inconclusive"]

# vLLM marks all sampled text, but text shorter than this cannot be detected
# with even a basic level of reliability, so a negative result says nothing
# about it. The threshold is the state of the art, not a
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


class _Claims(BaseModel):
    content_sha256: str


class _ReportClaims(_Claims):
    detector: str
    method: Literal["watermark"] = "watermark"
    status: _DetectionStatus


class _ProvenanceClaims(_Claims):
    ai_generated: Literal[True] = True


def get_transparency_config() -> TransparencyConfig:
    """Return the public subset of the deployment's transparency settings."""
    config = settings.transparency

    if config is None:
        return TransparencyConfig(
            enabled=False,
            contact_email=None,
            minimum_watermark_tokens=MINIMUM_WATERMARK_TOKENS,
        )

    return TransparencyConfig(
        enabled=True,
        contact_email=config.contact_email or None,
        minimum_watermark_tokens=MINIMUM_WATERMARK_TOKENS,
    )


@cache
def _signing_key(secret_key: str) -> OKPKey:
    """Return the signing key derived from *secret_key* with HKDF.

    Nothing is stored: the key is stable for as long as the secret is, so a
    report downloaded today still verifies tomorrow.
    """
    seed = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"hivegent",
        info=b"transparency-report-signing",
    ).derive(secret_key.encode())

    return OKPKey.import_key(Ed25519PrivateKey.from_private_bytes(seed))


def _key(config: TransparencySettings) -> OKPKey:
    return _signing_key(config.secret_key.get_secret_value())


def _issuer(config: TransparencySettings) -> str:
    return config.report_issuer.rstrip("/")


def _jwk(key: OKPKey) -> TransparencyJwk:
    return TransparencyJwk(x=str(key.as_dict(private=False)["x"]), kid=key.thumbprint())


def public_jwks(config: TransparencySettings) -> TransparencyJwks:
    """Return the keys that verify detection reports and export provenance.

    The active key comes first, followed by the retired keys that still verify
    what was signed before a rotation.
    """
    active = _jwk(_key(config))
    retired = (
        _jwk(OKPKey.import_key({"kty": "OKP", "crv": _ALG, "x": x}))
        for x in config.retired_public_keys
    )

    return TransparencyJwks(
        keys=[active, *(key for key in retired if key.kid != active.kid)]
    )


def _sign(config: TransparencySettings, claims: _Claims, typ: str) -> str:
    key = _key(config)
    payload = {"iss": _issuer(config), "iat": int(time.time()), **claims.model_dump()}

    return jws.serialize_compact(
        {"alg": _ALG, "typ": typ, "kid": key.thumbprint()},
        json.dumps(payload).encode(),
        key,
        algorithms=[_ALG],
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


def sign_provenance(
    config: TransparencySettings, conversation: ServerConversation
) -> str:
    """Return a signed statement that *conversation* contains AI-generated text.

    The digest covers the conversation without its ``provenance`` field,
    serialized as JSON with sorted keys, no whitespace and unescaped unicode,
    so a verifier recomputes it from the exported file.
    """
    canonical = json.dumps(
        conversation.model_dump(mode="json", exclude={"provenance"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return _sign(
        config,
        _ProvenanceClaims(content_sha256=content_digest(canonical)),
        PROVENANCE_TYPE,
    )


async def detect_text(
    config: TransparencySettings, text: str
) -> TransparencyDetectionResponse:
    """Detect the configured watermark without retaining the submitted text."""
    api_key = reveal(config.detector_api_key)

    try:
        response = await get_trusted_http_client().post(
            config.detector_url,
            json=_DetectorRequest(text=text).model_dump(),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
        )
        response.raise_for_status()
        result = _DetectorResponse.model_validate(response.json())
    except Exception as exc:
        raise TransparencyUnavailable("Watermark detector request failed") from exc

    status = _status(result)
    # The detection solution is the public endpoint, not the detector behind
    # it: naming the upstream URL would publish internal topology in a report
    # whose whole point is that anyone can re-run the check.
    claims = _ReportClaims(
        content_sha256=content_digest(text),
        detector=f"{_issuer(config)}/api/transparency/detect",
        status=status,
    )

    # The score and the p-value stay here: published, they are the feedback
    # signal an attacker needs to strip or forge the watermark.
    return TransparencyDetectionResponse(
        status=status,
        message=_message(status),
        signed_report=_sign(config, claims, "JWT"),
    )
