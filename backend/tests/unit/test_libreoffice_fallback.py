"""Tests for the LibreOffice text-recovery fallback."""

from pathlib import Path

import pytest

from hivegent.converters import libreoffice as lo


async def test_recovers_markdown_and_strips_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_convert(source: Path, out_dir: Path, *, to: str) -> Path:
        return out_dir / f"{source.stem}.html"

    async def fake_pandoc(source: Path, **_kwargs: object) -> str:
        return "# Title\n\n![alt](data:image/png;base64,AAAA)\n\nBody text."

    monkeypatch.setattr(lo, "libreoffice_convert", fake_convert)
    monkeypatch.setattr(lo, "pandoc_convert", fake_pandoc)

    recovered = await lo.recover_office_markdown(tmp_path / "report.docx")
    assert recovered is not None
    assert "Body text." in recovered
    assert "![" not in recovered  # image references dropped


async def test_returns_none_when_libreoffice_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def no_libreoffice(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(lo, "libreoffice_convert", no_libreoffice)

    assert await lo.recover_office_markdown(tmp_path / "report.docx") is None
