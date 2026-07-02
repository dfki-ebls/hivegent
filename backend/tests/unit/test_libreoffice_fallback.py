"""Tests for the LibreOffice text-recovery fallback."""

from pathlib import Path

import pytest

from hivegent.converters import libreoffice as lo


async def test_recovers_markdown_and_drops_images_via_pandoc_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured_args: list[tuple[str, ...]] = []

    async def fake_convert(source: Path, out_dir: Path, *, to: str) -> Path:
        return out_dir / f"{source.stem}.html"

    async def fake_pandoc(
        source: Path, *, extra_args: tuple[str, ...] = (), **_kwargs: object
    ) -> str:
        captured_args.append(extra_args)
        return "# Title\n\nBody text.\n"

    monkeypatch.setattr(lo, "libreoffice_convert", fake_convert)
    monkeypatch.setattr(lo, "pandoc_convert", fake_pandoc)

    recovered = await lo.recover_office_markdown(tmp_path / "report.docx")
    assert recovered == "# Title\n\nBody text."
    # Images are dropped by a pandoc AST filter, not a brittle text regex.
    assert "--lua-filter" in captured_args[0]


async def test_returns_none_when_libreoffice_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def no_libreoffice(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(lo, "libreoffice_convert", no_libreoffice)

    assert await lo.recover_office_markdown(tmp_path / "report.docx") is None
