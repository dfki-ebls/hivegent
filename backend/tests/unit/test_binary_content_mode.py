"""Tests for the ``images``/``native`` binary-content mode.

Covers the PDF rasteriser/extractor, the binary tool's per-mode PDF
output, and the chat-attachment rewrite that keeps PDF blobs off vision
servers that reject native ``file`` parts.
"""

import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from hivegent.converters.base import BinaryContentMode
from hivegent.converters.pdf_raster import (
    extract_pdf_pages,
    parse_pages,
    render_pdf_pages,
)
from hivegent.server.routes.conversations import _rasterize_pdf_attachments
from hivegent.tools.base import SearchPath
from hivegent.tools.binary import ReadBinaryDocumentTool


def _pdf(pages: int) -> bytes:
    """Build a blank *pages*-page A4 PDF."""
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(595, 842)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _pdf_tool(tmp_path: Path, mode: BinaryContentMode) -> ReadBinaryDocumentTool:
    """A binary tool over a temp 3-page ``doc.pdf``."""
    (tmp_path / "doc.pdf").write_bytes(_pdf(3))
    return ReadBinaryDocumentTool(
        paths=SearchPath(path=tmp_path), binary_content_mode=mode
    )


def test_parse_pages_and_extract_subset() -> None:
    assert parse_pages("1,3,5-7", 10) == (1, 3, 5, 6, 7)
    sub, selected = extract_pdf_pages(_pdf(4), "2-3")
    assert selected == (2, 3) and sub[:4] == b"%PDF"


def test_render_pdf_pages_caps_and_rejects() -> None:
    images, pages = render_pdf_pages(_pdf(2), None, 1024, 16)
    assert pages == (1, 2) and all(png[:4] == b"\x89PNG" for png in images)

    with pytest.raises(ValueError, match="exceeds the 1-page limit"):
        render_pdf_pages(_pdf(2), None, 1024, 1)
    with pytest.raises(ValueError, match="could not be opened"):
        render_pdf_pages(b"not a pdf", None, 1024, 16)


async def test_binary_tool_images_mode_renders_pages(tmp_path: Path) -> None:
    output = await _pdf_tool(tmp_path, BinaryContentMode.IMAGES)("doc.pdf", pages="1,3")
    assert output.data.pages == (1, 3)
    assert [a.media_type for a in output.attachments] == ["image/png", "image/png"]


async def test_binary_tool_native_mode_forwards_pdf(tmp_path: Path) -> None:
    output = await _pdf_tool(tmp_path, BinaryContentMode.NATIVE)("doc.pdf", pages="2")
    assert output.data.pages == (2,)
    assert [a.media_type for a in output.attachments] == ["application/pdf"]


def test_rasterize_pdf_attachments_rewrites_only_pdfs() -> None:
    png = BinaryContent(data=b"\x89PNG", media_type="image/png")
    part = UserPromptPart(
        content=["look", BinaryContent(data=_pdf(2), media_type="application/pdf"), png]
    )

    _rasterize_pdf_attachments([ModelRequest(parts=[part])])

    content = part.content
    assert isinstance(content, list)
    assert content[0] == "look" and png in content
    rendered = [c for c in content if isinstance(c, BinaryContent) and c is not png]
    assert [c.media_type for c in rendered] == ["image/png", "image/png"]
