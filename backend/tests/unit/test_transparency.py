"""Tests for watermark detection reports."""

import json

import pytest
from joserfc import jws
from joserfc.jwk import OKPKey

from hivegent import keys, transparency
from hivegent.config import settings

SECRET_KEY = "test-secret-key-with-enough-entropy"


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


@pytest.fixture(autouse=True)
def _derived_key_cache() -> object:
    """Keys are cached per process, so each test derives them afresh."""
    keys.derived_ed25519_key.cache_clear()
    transparency._signing_key.cache_clear()

    yield

    keys.derived_ed25519_key.cache_clear()
    transparency._signing_key.cache_clear()


async def test_detection_report_is_verifiable_and_contains_only_a_hash(
    monkeypatch,
) -> None:
    """A positive result is signed with the key derived from the app secret."""
    monkeypatch.setattr(settings, "secret_key", SECRET_KEY)
    monkeypatch.setattr(settings.transparency, "enabled", True)
    monkeypatch.setattr(settings.transparency, "detector_url", "http://detector/detect")
    monkeypatch.setattr(settings.transparency, "report_issuer", "https://example.test")
    monkeypatch.setattr(
        transparency,
        "get_trusted_http_client",
        lambda: _Client(_Response(is_watermarked=True, num_scored_tokens=250)),
    )

    result = await transparency.detect_text("marked text")
    jwk = transparency.public_jwks().keys[0].model_dump()
    signature = jws.deserialize_compact(
        result.signed_report,
        OKPKey.import_key(jwk),
        algorithms=["Ed25519"],
    )
    claims = json.loads(signature.payload)

    assert result.status == "detected"
    assert claims["status"] == "detected"
    assert claims["content_sha256"] == claims["sub"].removeprefix("sha256:")
    assert claims["detector"] == "https://example.test/api/transparency/detect"
    assert "marked text" not in signature.payload.decode()


def test_signing_key_is_derived_and_not_stored(monkeypatch) -> None:
    """The same secret reproduces the key, a different one replaces it."""
    monkeypatch.setattr(settings, "secret_key", SECRET_KEY)
    first = transparency.public_jwks().keys[0]

    transparency._signing_key.cache_clear()
    keys.derived_ed25519_key.cache_clear()
    monkeypatch.setattr(settings, "secret_key", "a-completely-different-secret-key")

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
