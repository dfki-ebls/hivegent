"""Tests for AUTO conversion routing of plain-text formats."""

from pathlib import Path

import pytest

from hivegent.converters import ConversionPipeline, resolve_auto_pipeline
from hivegent.converters.base import decode_text, fenced_code_block
from hivegent.converters.chonkie_text import ChonkieTextConverter


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Raw text routes to text-chef, not docling (which rejects ordinary
        # JSON as an invalid DoclingDocument) -- the headline regression.
        ("laws.json", ConversionPipeline.TEXT_CHEF),
        ("notes.txt", ConversionPipeline.TEXT_CHEF),
        ("app.log", ConversionPipeline.TEXT_CHEF),
        ("config.yaml", ConversionPipeline.TEXT_CHEF),
        # Structured text keeps its richer converter.
        ("data.csv", ConversionPipeline.DOCLING),
        # No extension cannot be placed and falls back to the LLM pipeline,
        # where the content-based fallback later salvages decodable text.
        ("laws2", ConversionPipeline.LLM),
    ],
)
def test_auto_routes_plain_text_to_text_chef(
    filename: str, expected: ConversionPipeline
) -> None:
    assert resolve_auto_pipeline(filename) is expected


def test_decode_text_gates_binary_from_text() -> None:
    assert decode_text(b'{"a": 1}') == '{"a": 1}'
    assert decode_text(b"\x89PNG\r\n") is None
    assert decode_text(b"text\x00with nul") is None


async def test_text_chef_fences_its_output(tmp_path: Path) -> None:
    # text-chef reads raw text verbatim and projects it as a fenced code block,
    # so the frontend renders it as source instead of misreading `#`/`*` as
    # markdown. Contained in the converter: it fires whenever text-chef runs
    # (AUTO's default for these formats), while an explicitly chosen converter
    # keeps its own output untouched.
    source = "# not a heading\n* not a list"
    doc = tmp_path / "notes.txt"
    doc.write_text(source)

    result = await ChonkieTextConverter()(doc)

    assert result.markdown.startswith("```txt")
    assert source in result.markdown


def test_fenced_code_block_escapes_embedded_fences() -> None:
    # A file that itself contains a long backtick run must not close the block
    # early: the fence grows to one backtick longer than the longest run.
    payload = "before\n````````\nafter"  # an 8-backtick line
    block = fenced_code_block(payload, ".md")
    fence = "`" * 9

    assert block == f"{fence}md\n{payload}\n{fence}\n"
    assert payload in block
