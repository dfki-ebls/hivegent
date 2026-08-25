"""Tests for AUTO conversion routing of plain-text formats."""

import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import Mock

import pytest

from hivegent.converters import ConversionPipeline, get_converter, resolve_auto_pipeline
from hivegent.converters.base import fenced_code_block
from hivegent.converters.plain_text import PlainTextConverter
from hivegent.types import LlmConfig, PipelineSpec
from hivegent.workspace import prepare


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Raw text routes to plain-text, not docling, which reads only its own
        # DoclingDocument JSON schema and the USPTO/JATS XML dialects and
        # rejects every ordinary file of those extensions -- the headline
        # regression for both suffixes.
        ("laws.json", ConversionPipeline.PLAIN_TEXT),
        ("catalog.xml", ConversionPipeline.PLAIN_TEXT),
        ("notes.txt", ConversionPipeline.PLAIN_TEXT),
        ("app.log", ConversionPipeline.PLAIN_TEXT),
        ("config.yaml", ConversionPipeline.PLAIN_TEXT),
        ("settings.ini", ConversionPipeline.PLAIN_TEXT),
        # Unclaimed source code reaches the content-aware plain-text default.
        ("server.py", ConversionPipeline.PLAIN_TEXT),
        # Structured text keeps its richer converter.
        ("data.csv", ConversionPipeline.DOCLING),
        # An unclaimed extension (and a name with none at all) falls back to
        # plain-text, which decides from the content rather than the name.
        ("laws2", ConversionPipeline.PLAIN_TEXT),
        ("archive.sbx", ConversionPipeline.PLAIN_TEXT),
    ],
)
def test_auto_routes_plain_text(filename: str, expected: ConversionPipeline) -> None:
    assert resolve_auto_pipeline(filename) is expected


@pytest.mark.skipif(find_spec("cysignals") is None, reason="docling extra absent")
def test_converters_package_preimports_cysignals() -> None:
    # cysignals installs signal handlers at import time and so imports cleanly
    # only on the main thread.  The package has to pull it in during its own
    # import, or the lazy docling load raises in the reconcile walk's worker
    # thread.  A subprocess is the only place the side effect is observable.
    code = "import sys, hivegent.converters; print('cysignals' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "True"


def test_auto_accepts_undeclared_extension() -> None:
    # The fallback pipeline must survive get_converter's extension check, which
    # would otherwise reject the very inputs it exists to catch.
    converter = get_converter(ConversionPipeline.AUTO, filename="archive.sbx")

    assert isinstance(converter, PlainTextConverter)


async def test_plain_text_fences_its_output(tmp_path: Path) -> None:
    # Raw text is projected as a fenced code block so the frontend renders it
    # as source instead of misreading `#`/`*` as markdown. The shared projection
    # keeps explicit conversion and AUTO preparation identical.
    source = "# not a heading\n* not a list"
    doc = tmp_path / "notes.txt"
    doc.write_text(source)

    result = await PlainTextConverter()(doc)

    assert result.markdown.startswith("```txt")
    assert source in result.markdown
    assert result.source_encoding is None


async def test_plain_text_transcodes_and_reports_encoding(tmp_path: Path) -> None:
    doc = tmp_path / "settings.ini"
    doc.write_bytes("Benutzer = Jörg Müller\n".encode("utf-16"))

    result = await PlainTextConverter()(doc)

    assert "Jörg Müller" in result.markdown
    assert result.source_encoding == "utf-16"


async def test_auto_plain_text_bypasses_converter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prepare,
        "get_converter",
        Mock(side_effect=AssertionError("converter must not be constructed")),
    )

    prepared = await prepare._prepare_convertible(
        "notes.txt",
        b"plain text",
        PipelineSpec(),
        LlmConfig(),
        origin="upload",
        ctx=None,
    )

    assert prepared.conversion_pipeline_used == ConversionPipeline.PLAIN_TEXT.value


async def test_plain_text_rejects_binary(tmp_path: Path) -> None:
    doc = tmp_path / "blob.sbx"
    doc.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(ValueError, match="not text"):
        await PlainTextConverter()(doc)


def test_auto_fallback_records_plain_text_provenance() -> None:
    # The fallback applies the same decode-and-fence projection as the pipeline
    # it names, so the recorded provenance describes what actually happened.
    prepared = prepare._prepare_plain_text_or_stub(
        "settings.ini", "Benutzer = Jörg\n".encode("cp1252"), origin="upload"
    )

    assert prepared.conversion_pipeline_used == ConversionPipeline.PLAIN_TEXT.value
    assert "Jörg" in prepared.main.markdown
    assert "decoded from" in prepared.message


def test_auto_fallback_stubs_real_binaries() -> None:
    prepared = prepare._prepare_plain_text_or_stub(
        "logo.bin", b"\x89PNG\r\n\x1a\n", origin="upload"
    )

    assert prepared.conversion_pipeline_used is None
    assert prepared.main.entry_metadata.entry_kind == "binary_stub"


def test_fenced_code_block_escapes_embedded_fences() -> None:
    # A file that itself contains a long backtick run must not close the block
    # early: the fence grows to one backtick longer than the longest run.
    payload = "before\n````````\nafter"  # an 8-backtick line
    block = fenced_code_block(payload, ".md")
    fence = "`" * 9

    assert block == f"{fence}md\n{payload}\n{fence}\n"
    assert payload in block
