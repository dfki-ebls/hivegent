"""Tests for AUTO conversion routing of plain-text formats."""

import pytest

from hivegent.converters import ConversionPipeline, resolve_auto_pipeline
from hivegent.converters.base import decode_text


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
