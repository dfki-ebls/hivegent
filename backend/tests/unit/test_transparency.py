"""Tests for watermark detection reports and export provenance."""

import json

from joserfc import jws
from joserfc.jwk import OKPKey
from pydantic import SecretStr

from hivegent import transparency
from hivegent.config import TransparencySettings, content_digest
from hivegent.types import ServerConversation

ISSUER = "https://example.test"

CONFIG = TransparencySettings(
    detector_url="http://detector/detect",
    report_issuer=ISSUER,
    secret_key=SecretStr("test-secret-key-with-enough-entropy"),
)

ROTATED = CONFIG.model_copy(
    update={"secret_key": SecretStr("a-completely-different-secret-key")}
)


class _Response:
    def __init__(self, *, is_watermarked: bool, num_scored_tokens: int) -> None:
        self.is_watermarked = is_watermarked
        self.num_scored_tokens = num_scored_tokens

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        return {
            "score": 8.1,
            "p_value": 0.0001,
            "num_scored_tokens": self.num_scored_tokens,
            "is_watermarked": self.is_watermarked,
        }


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def post(
        self,
        _url: str,
        *,
        json: object,
        headers: object,
    ) -> _Response:
        assert json == {"text": "marked text"}

        return self.response


def _verified(
    token: str, config: TransparencySettings = CONFIG
) -> tuple[dict[str, object], dict[str, object]]:
    """Verify *token* against the published key its ``kid`` names."""
    kid = jws.extract_compact(token.encode()).headers()["kid"]
    jwk = next(key for key in transparency.public_jwks(config).keys if key.kid == kid)
    signature = jws.deserialize_compact(
        token, OKPKey.import_key(jwk.model_dump()), algorithms=["Ed25519"]
    )

    return signature.headers(), json.loads(signature.payload)


async def test_detection_report_is_verifiable_and_contains_only_a_hash(
    monkeypatch,
) -> None:
    """A positive result is signed with the key derived from the app secret."""
    monkeypatch.setattr(
        transparency,
        "get_trusted_http_client",
        lambda: _Client(_Response(is_watermarked=True, num_scored_tokens=250)),
    )

    result = await transparency.detect_text(CONFIG, "marked text")
    header, claims = _verified(result.signed_report)

    assert result.status == "detected"
    assert claims["method"] == "watermark"
    assert claims["status"] == "detected"
    assert header["typ"] == "JWT"
    assert claims["detector"] == f"{ISSUER}/api/transparency/detect"
    assert "marked text" not in json.dumps(claims)


def test_export_provenance_covers_the_canonical_conversation() -> None:
    """A verifier recomputes the signed digest from the exported JSON."""
    conversation = ServerConversation(id="c1", title="Grüße")
    provenance = transparency.sign_provenance(CONFIG, conversation)
    conversation.provenance = provenance

    exported = json.loads(conversation.model_dump_json(indent=2))
    del exported["provenance"]
    canonical = json.dumps(
        exported, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    header, claims = _verified(provenance)

    assert header["typ"] == transparency.PROVENANCE_TYPE
    assert claims["ai_generated"] is True
    assert claims["content_sha256"] == content_digest(canonical)


def test_a_retired_key_still_verifies_after_rotation() -> None:
    """A report signed before a planned rotation verifies against the new JWKS."""
    report = transparency._sign(
        CONFIG,
        transparency._ReportClaims(
            content_sha256="0" * 64, detector=ISSUER, status="detected"
        ),
        "JWT",
    )
    retired = transparency.public_jwks(CONFIG).keys[0]
    rotated = ROTATED.model_copy(update={"retired_public_keys": [retired.x]})

    assert transparency.public_jwks(rotated).keys[0].x != retired.x
    assert _verified(report, rotated)[1]["status"] == "detected"


def test_short_negative_result_is_inconclusive() -> None:
    """Absence of signal below the Code's length threshold is not definitive."""
    result = transparency._DetectorResponse(
        score=0.2,
        p_value=0.4,
        num_scored_tokens=transparency.MINIMUM_WATERMARK_TOKENS - 1,
        is_watermarked=False,
    )

    assert transparency._status(result) == "inconclusive"
