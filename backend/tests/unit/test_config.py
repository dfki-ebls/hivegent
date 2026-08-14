"""Unit tests for config sanitization functions."""

import os
from pathlib import Path

import pytest

from hivegent.config import (
    CONFIG_FILE_ENV_VAR,
    Settings,
    sanitize_document_path,
    sanitize_group_id,
    sanitize_user_id,
)


class TestSanitizeDocumentPath:
    """Tests for sanitize_document_path."""

    def test_valid_simple(self) -> None:
        assert sanitize_document_path("report.md") == "report.md"

    def test_valid_nested(self) -> None:
        assert sanitize_document_path("projects/report.md") == "projects/report.md"

    def test_rejects_absolute(self) -> None:
        with pytest.raises(ValueError, match="relative"):
            sanitize_document_path("/etc/passwd")

    def test_rejects_dot_dot(self) -> None:
        with pytest.raises(ValueError, match="unsafe segment"):
            sanitize_document_path("../secret.md")

    def test_rejects_null_bytes(self) -> None:
        with pytest.raises(ValueError, match="null bytes"):
            sanitize_document_path("file\x00.md")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            sanitize_document_path("")

    def test_normalizes_dot_prefix(self) -> None:
        # PurePosixPath normalizes "./file.md" to "file.md"
        assert sanitize_document_path("./file.md") == "file.md"

    def test_normalizes_backslashes(self) -> None:
        assert sanitize_document_path("dir\\file.md") == "dir/file.md"

    def test_normalizes_decomposed_characters(self) -> None:
        # Escapes rather than literals: this file is saved precomposed, so
        # spelling both sides out would compare NFC with NFC and assert
        # nothing.  A macOS upload arrives decomposed while a model can only
        # ever emit the precomposed form, and both must reach the same path.
        assert sanitize_document_path("dir/SU\u0308VOA.md") == "dir/S\u00dcVOA.md"

    def test_keeps_compatibility_characters(self) -> None:
        # NFC, never NFKC: folding compatibility characters would silently
        # rename the document to "file.md".
        assert sanitize_document_path("\ufb01le.md") == "\ufb01le.md"


class TestSanitizeUserId:
    """Tests for sanitize_user_id."""

    def test_valid(self) -> None:
        assert sanitize_user_id("alice") == "alice"

    def test_valid_with_numbers(self) -> None:
        assert sanitize_user_id("user-123_test") == "user-123_test"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            sanitize_user_id("")

    def test_rejects_special_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid user ID"):
            sanitize_user_id("user@domain")


class TestSanitizeGroupId:
    """Tests for sanitize_group_id."""

    def test_valid(self) -> None:
        assert sanitize_group_id("engineering") == "engineering"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            sanitize_group_id("")

    def test_rejects_slashes(self) -> None:
        with pytest.raises(ValueError, match="Invalid group ID"):
            sanitize_group_id("group/sub")


def test_toml_config_overrides_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOML file overrides listed fields; env vars win; unlisted keep defaults."""
    for key in [
        k for k in os.environ if k.startswith("HIVEGENT_") and k != CONFIG_FILE_ENV_VAR
    ]:
        monkeypatch.delenv(key, raising=False)

    config = tmp_path / "config.toml"
    config.write_text(
        'data_dir = "/srv/hivegent"\n\n[network]\nconnect_timeout_seconds = 12.5\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_FILE_ENV_VAR, str(config))
    monkeypatch.setenv("HIVEGENT_NETWORK__WEBFETCH_TIMEOUT_SECONDS", "7")

    settings = Settings()

    assert settings.data_dir == Path("/srv/hivegent")
    assert settings.network.connect_timeout_seconds == 12.5
    assert settings.network.webfetch_timeout_seconds == 7.0
    assert settings.limits.max_file_size_bytes == 50 * 1024 * 1024
