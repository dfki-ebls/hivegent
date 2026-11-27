"""Tests for AUTO conversion routing and the plain-text projection."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from exar.ingest import (
    Anydoc,
    Docling,
    Offloaded,
    Parsed,
    Pdftotext,
    PlainText,
    WorkerCrashed,
    garbled,
)

from hivegent import pipelines
from hivegent.pipelines import AUTO, _Recovering, get_converter, resolve_auto_pipeline
from hivegent.shared import pipeline_registry
from hivegent.workspace import prepare


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Raw text routes to plain-text, not docling, which reads only its own
        # DoclingDocument JSON schema and the USPTO/JATS XML dialects.
        ("laws.json", "plain-text"),
        ("catalog.xml", "plain-text"),
        ("notes.txt", "plain-text"),
        ("app.log", "plain-text"),
        ("config.yaml", "plain-text"),
        ("settings.ini", "plain-text"),
        # Unclaimed source code reaches the content-aware plain-text default.
        ("server.py", "plain-text"),
        # Structured text keeps its richer converter.
        ("data.csv", "docling"),
        ("archive.tar.gz", "docling"),
        ("report.docx", "docling"),
        # Office formats docling is not given go to anydoc.
        ("letter.doc", "anydoc"),
        ("sheet.ods", "anydoc"),
        # An unclaimed extension, and a name with none at all, fall back to
        # plain-text, which decides from the content rather than the name.
        ("laws2", "plain-text"),
        ("archive.sbx", "plain-text"),
    ],
)
def test_auto_routes_plain_text(
    monkeypatch: pytest.MonkeyPatch, filename: str, expected: str
) -> None:
    # The routing every converter declares, whichever extras are installed.
    monkeypatch.setattr(pipeline_registry, "_installed", lambda *_: True)

    assert resolve_auto_pipeline(filename) == expected


def test_auto_conversion_imports_no_backend() -> None:
    code = (
        "import sys\n"
        "from hivegent.pipelines import get_converter\n"
        "get_converter('auto', 'report.docx')\n"
        "print('docling' in sys.modules, 'anydoc' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert result.stdout.strip() == "False False"


def _installed(monkeypatch: pytest.MonkeyPatch, name: str, installed: bool) -> None:
    registration = pipelines.CONVERTERS[name]
    dependencies = () if installed else ("hivegent_missing_module",)
    monkeypatch.setattr(
        pipelines,
        "CONVERTERS",
        {
            **pipelines.CONVERTERS,
            name: replace(registration, dependencies=dependencies),
        },
    )


def test_auto_uses_next_available_converter(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "docling", False)

    converter = get_converter("auto", filename="data.csv")

    assert isinstance(converter, Offloaded)
    assert isinstance(converter.func, Anydoc)


def test_auto_falls_back_for_garbled_pdfs(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "docling", True)

    converter = get_converter("auto", filename="report.pdf")

    assert isinstance(converter, _Recovering)
    assert isinstance(converter.primary, Offloaded)
    assert isinstance(converter.primary.func, Docling)
    assert isinstance(converter.fallback, Offloaded)
    assert converter.fallback.func == Pdftotext()


async def test_auto_falls_back_when_the_worker_crashes(tmp_path: Path) -> None:
    async def crash(source: Path, /) -> Parsed:
        raise WorkerCrashed("The worker exited with code -9")

    async def recover(source: Path, /) -> Parsed:
        return Parsed(markdown="recovered")

    for degraded in (None, garbled):
        converter = _Recovering(crash, recover, degraded)

        assert (await converter(tmp_path / "a.pdf")).markdown == "recovered"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notes.rst", "kreuzberg"),
        ("paper.typ", "kreuzberg"),
        ("page.textile", "plain-text"),
    ],
)
def test_auto_routes_markup_to_kreuzberg(
    monkeypatch: pytest.MonkeyPatch, filename: str, expected: str
) -> None:
    _installed(monkeypatch, "kreuzberg", True)

    assert resolve_auto_pipeline(filename) == expected


def test_auto_accepts_undeclared_extension() -> None:
    # The fallback pipeline must survive get_converter's extension check, which
    # would otherwise reject the very inputs it exists to catch.
    converter = get_converter("auto", filename="archive.sbx")

    assert isinstance(converter, Offloaded)
    assert converter.func == PlainText()


async def test_auto_plain_text_uses_plain_text_pipeline(
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
        AUTO,
        origin="upload",
        ctx=None,
    )

    assert prepared.main is not None
    assert prepared.main.entry_metadata.entry_kind == "convertible"
    assert "plain text" in prepared.main.markdown


def test_auto_fallback_decodes_legacy_text() -> None:
    entry = prepare._prepare_plain_text_or_stub(
        "settings.ini", "Benutzer = Jörg\n".encode("cp1252"), origin="upload"
    )

    assert entry.entry_metadata.entry_kind == "convertible"
    assert "Jörg" in entry.markdown


def test_auto_fallback_stubs_real_binaries() -> None:
    entry = prepare._prepare_plain_text_or_stub(
        "logo.bin", b"\x89PNG\r\n\x1a\n", origin="upload"
    )

    assert entry.entry_metadata.entry_kind == "binary_stub"
