"""Tests for watermark detection reports and export provenance."""

import json

import pytest
from joserfc import jws
from joserfc.jwk import OKPKey

from hivegent import keys, transparency
from hivegent.config import content_digest, settings
from hivegent.types import ServerConversation

SECRET_KEY = "test-secret-key-with-enough-entropy"

ISSUER = "https://example.test"


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


def _clear_key_caches() -> None:
    keys.derived_ed25519_key.cache_clear()
    transparency._signing_key.cache_clear()
    transparency.public_jwks.cache_clear()


@pytest.fixture(autouse=True)
def _derived_key_cache() -> object:
    """Keys are cached per process, so each test derives them afresh."""
    _clear_key_caches()

    yield

    _clear_key_caches()


def _rotate_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_key_caches()
    monkeypatch.setattr(settings, "secret_key", "a-completely-different-secret-key")


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secret_key", SECRET_KEY)
    monkeypatch.setattr(settings.transparency, "enabled", True)
    monkeypatch.setattr(settings.transparency, "report_issuer", ISSUER)


def _verified_claims(token: str) -> dict[str, object]:
    """Verify *token* against the published key its ``kid`` names."""
    kid = jws.extract_compact(token.encode()).headers()["kid"]
    jwk = next(key for key in transparency.public_jwks().keys if key.kid == kid)
    signature = jws.deserialize_compact(
        token, OKPKey.import_key(jwk.model_dump()), algorithms=["Ed25519"]
    )

    return json.loads(signature.payload)


async def test_detection_report_is_verifiable_and_contains_only_a_hash(
    monkeypatch,
) -> None:
    """A positive result is signed with the key derived from the app secret."""
    _enable(monkeypatch)
    monkeypatch.setattr(settings.transparency, "detector_url", "http://detector/detect")
    monkeypatch.setattr(
        transparency,
        "get_trusted_http_client",
        lambda: _Client(_Response(is_watermarked=True, num_scored_tokens=250)),
    )

    result = await transparency.detect_text("marked text")
    claims = _verified_claims(result.signed_report)

    assert result.status == "detected"
    assert claims["method"] == "watermark"
    assert claims["status"] == "detected"
    assert claims["sub"] == f"sha256:{claims['content_sha256']}"
    assert claims["detector"] == f"{ISSUER}/api/transparency/detect"
    assert "marked text" not in json.dumps(claims)


def test_export_provenance_covers_the_canonical_conversation(monkeypatch) -> None:
    """A verifier recomputes the signed digest from the exported JSON."""
    _enable(monkeypatch)
    conversation = ServerConversation(id="c1", title="Grüße")
    provenance = transparency.sign_provenance(conversation)
    assert provenance is not None
    conversation.provenance = provenance

    exported = json.loads(conversation.model_dump_json(indent=2))
    del exported["provenance"]
    canonical = json.dumps(
        exported, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    claims = _verified_claims(provenance)

    assert claims["ai_generated"] is True
    assert claims["content_sha256"] == content_digest(canonical)


def test_a_retired_key_still_verifies_after_rotation(monkeypatch) -> None:
    """A report signed before a planned rotation verifies against the new JWKS."""
    _enable(monkeypatch)
    report = transparency._sign(
        transparency._ReportClaims(content_sha256="0" * 64, status="detected")
    )
    retired = transparency.public_jwks().keys[0]

    _rotate_secret(monkeypatch)
    monkeypatch.setattr(settings.transparency, "retired_public_keys", [retired.x])

    assert transparency.public_jwks().keys[0].kid != retired.kid
    assert _verified_claims(report)["status"] == "detected"


def test_signing_key_is_derived_and_not_stored(monkeypatch) -> None:
    """The same secret reproduces the key, a different one replaces it."""
    monkeypatch.setattr(settings, "secret_key", SECRET_KEY)
    first = transparency.public_jwks().keys[0]

    _rotate_secret(monkeypatch)

    assert transparency.public_jwks().keys[0].x != first.x


def test_a_weak_secret_is_refused(monkeypatch) -> None:
    """Deriving a signing key from a short secret fails loudly."""
    monkeypatch.setattr(settings, "secret_key", "too-short")

    with pytest.raises(keys.MissingSecretKey):
        transparency.public_jwks()


def test_short_negative_result_is_inconclusive() -> None:
    """Absence of signal below the Code's length threshold is not definitive."""
    result = transparency._DetectorResponse(
        score=0.2,
        p_value=0.4,
        num_scored_tokens=transparency.MINIMUM_WATERMARK_TOKENS - 1,
        is_watermarked=False,
    )

    assert transparency._status(result) == "inconclusive"
