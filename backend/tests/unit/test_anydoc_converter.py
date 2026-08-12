"""Tests for the anydoc converter."""

from pathlib import Path

import pytest

from hivegent.converters import ConversionPipeline, get_converter
from hivegent.converters.anydoc import AnydocConverter


async def test_anydoc_converts_structure_to_markdown(tmp_path: Path) -> None:
    doc = tmp_path / "note.rtf"
    doc.write_text(r"{\rtf1\ansi \b Heading\b0 \par Plain body text.\par}")

    result = await AnydocConverter()(doc)

    assert "**Heading**" in result.markdown
    assert "Plain body text." in result.markdown
    assert result.images == {}


def test_anydoc_leaves_pdfs_to_pdf_inspector() -> None:
    # ``.pdf`` is deliberately outside the declared extensions; see the
    # converter's docstring for why.
    with pytest.raises(ValueError, match="does not support .pdf"):
        get_converter(ConversionPipeline.ANYDOC, filename="report.pdf")
