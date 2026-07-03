"""Tests for the conversion fallback registry routing."""

from pathlib import Path

from hivegent.converters import ConversionPipeline
from hivegent.converters.base import ConversionResult
from hivegent.converters.fallbacks import Fallback, recover_conversion


def _registry(recovered: str | None) -> tuple[Fallback, ...]:
    """A two-entry registry whose recoveries all yield *recovered*."""

    async def recover(_source: Path) -> str | None:
        return recovered

    return (
        Fallback(
            pipeline=ConversionPipeline.LIBREOFFICE,
            suffixes=frozenset({".docx"}),
            trigger=lambda outcome: isinstance(outcome, Exception),
            recover=recover,
        ),
        Fallback(
            pipeline=ConversionPipeline.POPPLER,
            suffixes=frozenset({".pdf"}),
            trigger=lambda outcome: isinstance(outcome, ConversionResult),
            recover=recover,
        ),
    )


async def test_dispatches_by_suffix_and_trigger(tmp_path: Path) -> None:
    registry = _registry("# recovered")

    # Office suffix + raised error -> the LibreOffice entry.
    office = await recover_conversion(
        tmp_path / "a.docx", ".docx", RuntimeError("boom"), registry=registry
    )
    assert office is not None
    assert office.pipeline is ConversionPipeline.LIBREOFFICE

    # PDF suffix + degraded (but successful) result -> the poppler entry.
    pdf = await recover_conversion(
        tmp_path / "a.pdf", ".pdf", ConversionResult(markdown="x"), registry=registry
    )
    assert pdf is not None
    assert pdf.pipeline is ConversionPipeline.POPPLER
    assert pdf.markdown == "# recovered"


async def test_returns_none_when_nothing_applies(tmp_path: Path) -> None:
    result = ConversionResult(markdown="x")

    # No entry handles this suffix.
    assert (
        await recover_conversion(
            tmp_path / "a.txt", ".txt", RuntimeError(), registry=_registry("md")
        )
        is None
    )
    # The suffix matches but the trigger does not fire (pdf entry needs a result).
    assert (
        await recover_conversion(
            tmp_path / "a.pdf", ".pdf", RuntimeError(), registry=_registry("md")
        )
        is None
    )
    # A matching entry whose recovery yields nothing leaves the caller's output.
    assert (
        await recover_conversion(
            tmp_path / "a.pdf", ".pdf", result, registry=_registry(None)
        )
        is None
    )
