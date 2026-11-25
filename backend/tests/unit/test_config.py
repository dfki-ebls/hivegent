"""Unit tests for config sanitization functions."""

import os
from pathlib import Path

import pytest

from hivegent.config import (
    Settings,
    sanitize_document_path,
    sanitize_group_id,
    sanitize_user_id,
)
from hivegent.config.llm import LlmSettings
from hivegent.config.observability import TransparencySettings
from hivegent.config.retrieval import RemoteEmbeddingSettings
from hivegent.config.root import CONFIG_FILE_ENV_VAR
from hivegent.config.security import AuthSettings


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

    def test_rejects_the_name_of_a_partial_file(self) -> None:
        with pytest.raises(ValueError, match="unsafe segment"):
            sanitize_document_path("dir/.~0123456789abcdef.partial")

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
    monkeypatch.setenv("HIVEGENT_NETWORK__WEB_TIMEOUT_SECONDS", "7")

    settings = Settings()

    assert settings.data_dir == Path("/srv/hivegent")
    assert settings.network.connect_timeout_seconds == 12.5
    assert settings.network.web_timeout_seconds == 7.0
    assert settings.limits.max_file_size_bytes == 50 * 1024 * 1024


def test_credential_files_override_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file in ``$CREDENTIALS_DIRECTORY`` supplies a nested secret setting."""
    (tmp_path / "hivegent.TRANSPARENCY__SECRET_KEY").write_text("x" * 32 + "\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("HIVEGENT_TRANSPARENCY__ENDPOINT", "local")
    monkeypatch.setenv("HIVEGENT_TRANSPARENCY__DETECTOR_PATH", "/detect")
    monkeypatch.setenv(
        "HIVEGENT_LLM__PROVIDERS__LOCAL__BASE_URL", "http://localhost/v1"
    )
    monkeypatch.setenv("HIVEGENT_TRANSPARENCY__REPORT_ISSUER", "https://hivegent.test")

    transparency = Settings().transparency

    assert transparency is not None
    assert transparency.secret_key.get_secret_value() == "x" * 32
    assert "x" * 32 not in repr(transparency)
    with pytest.raises(ValueError, match="secret_key"):
        TransparencySettings.model_validate(
            {**transparency.model_dump(), "secret_key": "too-short"}
        )


def test_remote_embedding_names_a_catalog_endpoint() -> None:
    """A remote embedder borrows a provider's credentials, so it must name one that exists."""
    with pytest.raises(ValueError, match="endpoint"):
        RemoteEmbeddingSettings.model_validate(
            {"provider": "openai", "model": "m", "dimension": 8}
        )

    with pytest.raises(ValueError, match="unknown provider"):
        Settings(
            embedding=RemoteEmbeddingSettings(
                provider="openai", endpoint="nope", model="m", dimension=8
            )
        )


def test_a_family_needs_a_runtime_that_takes_it() -> None:
    """vLLM profiles a model by its served name, so a family there would be silently ignored."""
    catalog = {
        "models": {"chat": {"provider": "local", "model": "m", "family": "qwen"}},
        "default": "chat",
    }

    for runtime in ("llama.cpp", "sglang", "openai-compatible"):
        provider = {
            "base_url": "http://127.0.0.1:8000/v1",
            "inference_provider": runtime,
        }
        _ = LlmSettings.model_validate({**catalog, "providers": {"local": provider}})

    with pytest.raises(ValueError, match="ignores"):
        provider = {
            "base_url": "http://127.0.0.1:8000/v1",
            "inference_provider": "vllm",
        }
        _ = LlmSettings.model_validate({**catalog, "providers": {"local": provider}})


def test_a_removed_setting_stops_the_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown keys are refused in every section, from the TOML file and the environment."""
    for name in os.environ:
        if name.startswith("HIVEGENT_"):
            monkeypatch.delenv(name)

    config = tmp_path / "config.toml"
    config.write_text('[llm]\nmodel = "gpt"\n')
    monkeypatch.setenv(CONFIG_FILE_ENV_VAR, str(config))

    with pytest.raises(ValueError, match=r"llm\.model"):
        _ = Settings()

    config.write_text("")
    monkeypatch.setenv("HIVEGENT_SECURITY__USER_URLS", "[]")

    with pytest.raises(ValueError, match=r"security\.user_urls"):
        _ = Settings()

    monkeypatch.delenv("HIVEGENT_SECURITY__USER_URLS")
    monkeypatch.setenv("HIVEGENT_AUTH_ISSUER", "https://idp.test")

    with pytest.raises(ValueError, match="HIVEGENT_AUTH_ISSUER"):
        _ = Settings()


def test_a_named_config_file_must_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``HIVEGENT_CONFIG_FILE`` naming no file stops the boot instead of reading nothing."""
    monkeypatch.setenv(CONFIG_FILE_ENV_VAR, str(tmp_path / "missing.toml"))

    with pytest.raises(FileNotFoundError, match="missing.toml"):
        _ = Settings()


@pytest.mark.parametrize(
    ("issuer", "accepted"),
    [
        ("https://idp.example/realms/x", True),
        ("http://localhost:8081/auth/v1", True),
        ("http://auth.localhost:8081/auth/v1/", True),
        ("http://127.0.0.1:8081", True),
        ("http://[::1]:8081", True),
        ("http://idp.example", False),
        ("http://localhost.evil.example", False),
    ],
)
def test_the_issuer_is_https_unless_on_a_loopback_host(
    issuer: str, accepted: bool
) -> None:
    if accepted:
        assert AuthSettings.model_validate({"issuer": issuer}).issuer == issuer
    else:
        with pytest.raises(ValueError, match="loopback"):
            _ = AuthSettings.model_validate({"issuer": issuer})
