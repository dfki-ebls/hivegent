"""Tests for AUTO conversion routing and the plain-text projection."""

from collections.abc import Callable
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
from hivegent.pipelines import (
    AUTO,
    _Recovering,
    converter_fingerprint,
    get_converter,
    plain_text,
    resolve_auto_pipeline,
)
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
        # Markup goes to kreuzberg where it reads it.
        ("notes.rst", "kreuzberg"),
        ("paper.typ", "kreuzberg"),
        ("page.textile", "plain-text"),
    ],
)
def test_auto_routes_by_extension(
    monkeypatch: pytest.MonkeyPatch, filename: str, expected: str
) -> None:
    # The routing every converter declares, whichever extras are installed.
    monkeypatch.setattr(pipeline_registry, "_installed", lambda *_: True)

    assert resolve_auto_pipeline(filename) == expected


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


@pytest.mark.parametrize("degraded", [None, garbled])
async def test_auto_falls_back_when_the_worker_crashes(
    tmp_path: Path, degraded: Callable[[Parsed], bool] | None
) -> None:
    async def crash(source: Path, /) -> Parsed:
        raise WorkerCrashed("The worker exited with code -9")

    async def recover(source: Path, /) -> Parsed:
        return Parsed(markdown="recovered")

    converter = _Recovering(crash, recover, degraded)

    assert (await converter(tmp_path / "a.pdf")).markdown == "recovered"


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
    assert prepared.main.entry_metadata.converter == converter_fingerprint(plain_text())
    assert "plain text" in prepared.main.markdown


@pytest.mark.parametrize(
    ("filename", "content", "kind", "text"),
    [
        ("settings.ini", "Benutzer = Jörg\n".encode("cp1252"), "convertible", "Jörg"),
        ("logo.bin", b"\x89PNG\r\n\x1a\n", "binary_stub", ""),
    ],
)
def test_auto_fallback_decodes_legacy_text_and_stubs_real_binaries(
    filename: str, content: bytes, kind: str, text: str
) -> None:
    entry = prepare._prepare_plain_text_or_stub(
        filename, content, origin="upload", converter=None
    )

    assert entry.entry_metadata.entry_kind == kind
    assert text in entry.markdown


def test_converter_fingerprint_names_the_converter_auto_routed_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_registry, "_installed", lambda *_: True)
    converter = get_converter(AUTO, filename="report.docx")

    assert isinstance(converter, _Recovering)
    assert converter_fingerprint(converter) == converter_fingerprint(converter.primary)


def test_converter_fingerprint_is_none_for_a_converter_pydantic_cannot_dump() -> None:
    class Arbitrary:
        async def __call__(self, source: Path, /) -> Parsed:
            return Parsed(markdown=source.name)

    assert converter_fingerprint(Arbitrary()) is None
