"""Tests for the shared bytes-to-text decoder."""

from pathlib import Path

import pytest

from hivegent.text import decode_bytes, read_text_file

_GERMAN = (
    "[Allgemein]\n"
    "; Konfiguration für die Übersicht der Geschäftsprozesse\n"
    "Benutzer = Jörg Müller\n"
    "Beschreibung = Diese Datei enthält alle Einstellungen für den Zugriff.\n"
)


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", "utf-8-sig", "utf-16", "utf-32", "cp1252"],
)
def test_legacy_encodings_round_trip(encoding: str) -> None:
    # Every encoding a Windows-authored config or XML export realistically
    # carries decodes to the same text, and names the encoding it came from so
    # the caller can report the transcode instead of hiding it.
    decoded = decode_bytes(_GERMAN.encode(encoding))

    assert decoded is not None
    assert decoded.text == _GERMAN
    assert (decoded.source_encoding is None) is (encoding == "utf-8")


@pytest.mark.parametrize(
    ("text", "encoding"),
    [("Привет", "utf-16"), ("Ā", "utf-32")],
)
def test_bom_unicode_is_decoded_before_binary_checks(
    text: str, encoding: str
) -> None:
    decoded = decode_bytes(text.encode(encoding))

    assert decoded is not None
    assert decoded.text == text


def test_bomless_wide_unicode_is_not_guessed() -> None:
    assert decode_bytes(_GERMAN.encode("utf-16-be")) is None


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"\x89PNG\r\n\x1a\n", id="signature-only-binary"),
        pytest.param(b"PK\x03\x04", id="zip-header"),
        pytest.param(b"\x7fELF", id="elf-header"),
        pytest.param(b"text\x00with nul", id="nul-in-decodable-bytes"),
    ],
)
def test_binary_content_is_rejected(content: bytes) -> None:
    # A control byte is proof of binary content even in a sample far too short
    # for statistical detection, which would otherwise map it onto some exotic
    # codepage and hand back mojibake.
    assert decode_bytes(content) is None


def test_text_controls_stay_text() -> None:
    # Terminal escapes and form feeds are ordinary log content, not a binary
    # tell, so they must survive the gate.
    assert decode_bytes(b"\x1b[31mERROR\x1b[0m page\x0cbreak\n") is not None


def test_read_text_file_decodes_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "settings.ini"
    path.write_bytes(_GERMAN.encode("cp1252"))

    decoded = read_text_file(path)

    assert decoded is not None
    assert decoded.text == _GERMAN
    assert decoded.source_encoding == "cp1252"
