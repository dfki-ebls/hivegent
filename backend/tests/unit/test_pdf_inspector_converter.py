"""Tests for the pdf-inspector converter and its text-layer probe."""

from pathlib import Path

import pytest

from hivegent.converters.pdf_classify import pdf_has_text_layer
from hivegent.converters.pdf_inspector import (
    PdfInspectorConverter,
    PdfInspectorConverterConfig,
)


def _write_pdf(path: Path, text: str) -> Path:
    """Write a minimal single-page PDF drawing *text* in a standard font.

    Hand-assembled rather than fixture bytes so the content under test stays
    readable, and with a real cross-reference table so the parser takes its
    normal path instead of its damaged-file recovery.
    """
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>"
        ),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj%s endobj\n" % (number, body)

    start_xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start_xref,
    )

    path.write_bytes(bytes(out))
    return path


async def test_converts_text_layer_to_markdown(tmp_path: Path) -> None:
    doc = _write_pdf(tmp_path / "report.pdf", "Hivegent Fixture Heading")

    result = await PdfInspectorConverter()(doc)

    assert "Hivegent Fixture Heading" in result.markdown
    assert "<!-- Page" not in result.markdown
    assert result.images == {}


async def test_page_markers_annotate_each_page(tmp_path: Path) -> None:
    doc = _write_pdf(tmp_path / "report.pdf", "Hivegent Fixture Heading")
    config = PdfInspectorConverterConfig(page_markers=True)

    result = await PdfInspectorConverter(config=config)(doc)

    assert result.markdown.startswith("<!-- Page 1 -->")
    assert "Hivegent Fixture Heading" in result.markdown


async def test_pdf_without_text_layer_raises(tmp_path: Path) -> None:
    # The converter runs no OCR, so a PDF it cannot read must fail loudly
    # rather than index an empty document.
    doc = _write_pdf(tmp_path / "scan.pdf", "")

    with pytest.raises(ValueError, match="no extractable text layer"):
        await PdfInspectorConverter()(doc)


def test_text_layer_probe_gates_ocr(tmp_path: Path) -> None:
    # The probe decides whether docling may skip its Tesseract stage; an
    # unreadable file must answer "no text layer" so OCR still runs.
    assert pdf_has_text_layer(_write_pdf(tmp_path / "born-digital.pdf", "Native text"))

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    assert not pdf_has_text_layer(broken)
