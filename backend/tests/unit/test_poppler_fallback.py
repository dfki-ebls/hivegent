"""Tests for the poppler PDF text-recovery fallback."""

from pathlib import Path

import pytest

from hivegent.converters import poppler


def test_garble_gate_flags_glyph_dump_and_passes_real_text() -> None:
    garbled = "## " + "".join(f"/G{c:02X}" for c in b"Volumenstromangaben")
    assert poppler.is_pdf_text_garbled(garbled)
    assert not poppler.is_pdf_text_garbled(
        "# Volumenstromangaben in der Drucklufttechnik\n\nDer Druckluftverbrauch."
    )
    # A handful of slash-tokens (paths, options) must not trip the gate.
    assert not poppler.is_pdf_text_garbled("See /usr/bin and /etc for the config.")


async def test_recover_returns_text_when_pdftotext_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_pdftotext(source: Path, **_kwargs: object) -> str:
        return "  Volumenstromangaben in der Drucklufttechnik  \n"

    monkeypatch.setattr(poppler, "pdftotext_convert", fake_pdftotext)

    recovered = await poppler.recover_pdf_markdown(tmp_path / "doc.pdf")
    assert recovered == "Volumenstromangaben in der Drucklufttechnik"


async def test_recover_returns_none_when_poppler_missing_or_still_garbled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def missing_binary(*_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(poppler, "pdftotext_convert", missing_binary)
    assert await poppler.recover_pdf_markdown(tmp_path / "doc.pdf") is None

    async def still_garbled(*_args: object, **_kwargs: object) -> str:
        return "".join(f"/G{c:02X}" for c in b"stillbroken")

    monkeypatch.setattr(poppler, "pdftotext_convert", still_garbled)
    assert await poppler.recover_pdf_markdown(tmp_path / "doc.pdf") is None
