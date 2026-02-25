"""Unit tests for config sanitization functions."""

import pytest

from hivegent.config import sanitize_document_path, sanitize_group_id, sanitize_user_id


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
