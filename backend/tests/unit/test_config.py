"""Unit tests for config sanitization and settings loading."""

from collections.abc import Callable
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
from hivegent.config.retrieval import LocalEmbeddingSettings, RemoteEmbeddingSettings
from hivegent.config.root import CONFIG_FILE_ENV_VAR
from hivegent.config.security import AuthSettings

LOCAL = "http://127.0.0.1:8000/v1"


type Load = Callable[..., Settings]


@pytest.fixture()
def load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Load:
    """``Settings(embedding=embedding)`` as loaded from *toml* and *env*, which only that call sees.

    The credentials of the host are kept out of it, unless *env* names them.
    """

    def settings(
        toml: str | None = None,
        *,
        embedding: LocalEmbeddingSettings | RemoteEmbeddingSettings | None = None,
        **env: str,
    ) -> Settings:
        with monkeypatch.context() as patch:
            patch.delenv("CREDENTIALS_DIRECTORY", raising=False)

            if toml is not None:
                config = tmp_path / "config.toml"
                config.write_text(toml)
                patch.setenv(CONFIG_FILE_ENV_VAR, str(config))

            for name, value in env.items():
                patch.setenv(name, value)

            if embedding is None:
                return Settings()

            return Settings(embedding=embedding)

    return settings


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("projects/report.md", "projects/report.md"),
        ("./file.md", "file.md"),
        ("dir\\file.md", "dir/file.md"),
        # Escapes, as the file is saved precomposed: a macOS upload arrives
        # decomposed and a model emits the precomposed form, both must meet.
        ("dir/SÜVOA.md", "dir/SÜVOA.md"),
        # NFC and never NFKC, which would rename the document to "file.md".
        ("ﬁle.md", "ﬁle.md"),
    ],
)
def test_document_paths_are_normalized(path: str, expected: str) -> None:
    assert sanitize_document_path(path) == expected


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/etc/passwd", "relative"),
        ("../secret.md", "unsafe segment"),
        ("dir/.~0123456789abcdef.partial", "unsafe segment"),
        ("file\x00.md", "null bytes"),
        ("", "empty"),
    ],
)
def test_unsafe_document_paths_are_rejected(path: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        sanitize_document_path(path)


@pytest.mark.parametrize(
    ("sanitize", "name", "error"),
    [
        (sanitize_user_id, "", "empty"),
        (sanitize_user_id, "user@domain", "Invalid user ID"),
        (sanitize_group_id, "", "empty"),
        (sanitize_group_id, "group/sub", "Invalid group ID"),
    ],
)
def test_invalid_ids_are_rejected(
    sanitize: Callable[[str], str], name: str, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        sanitize(name)


def test_toml_config_overrides_defaults(load: Load) -> None:
    """TOML file overrides listed fields, env vars win, unlisted keep defaults."""
    settings = load(
        'data_dir = "/srv/hivegent"\n\n[network]\nconnect_timeout_seconds = 12.5\n',
        HIVEGENT_NETWORK__WEB_TIMEOUT_SECONDS="7",
    )

    assert settings.data_dir == Path("/srv/hivegent")
    assert settings.network.connect_timeout_seconds == 12.5
    assert settings.network.web_timeout_seconds == 7.0
    assert settings.limits.max_file_size_bytes == 50 * 1024 * 1024


def test_credential_files_supply_nested_secrets(load: Load, tmp_path: Path) -> None:
    (tmp_path / "hivegent.TRANSPARENCY__SECRET_KEY").write_text("x" * 32 + "\n")

    transparency = load(
        CREDENTIALS_DIRECTORY=str(tmp_path),
        HIVEGENT_TRANSPARENCY__ENDPOINT="local",
        HIVEGENT_TRANSPARENCY__DETECTOR_PATH="/detect",
        HIVEGENT_LLM__PROVIDERS__LOCAL__BASE_URL=LOCAL,
        HIVEGENT_TRANSPARENCY__REPORT_ISSUER="https://hivegent.test",
    ).transparency

    assert transparency is not None
    assert transparency.secret_key.get_secret_value() == "x" * 32
    assert "x" * 32 not in repr(transparency)

    with pytest.raises(ValueError, match="secret_key"):
        TransparencySettings.model_validate(
            {**transparency.model_dump(), "secret_key": "too-short"}
        )


def test_remote_embedding_names_a_catalog_endpoint(load: Load) -> None:
    """A remote embedder borrows a provider's credentials, so it must name one that exists."""
    with pytest.raises(ValueError, match="endpoint"):
        RemoteEmbeddingSettings.model_validate(
            {"provider": "openai", "model": "m", "dimension": 8}
        )

    with pytest.raises(ValueError, match="unknown provider"):
        _ = load(
            embedding=RemoteEmbeddingSettings(
                provider="openai", endpoint="nope", model="m", dimension=8
            )
        )


@pytest.mark.parametrize(
    ("runtime", "accepted"),
    [
        ("llama.cpp", True),
        ("sglang", True),
        ("openai-compatible", True),
        ("vllm", False),
    ],
)
def test_a_family_needs_a_runtime_that_takes_it(runtime: str, accepted: bool) -> None:
    """vLLM profiles a model by its served name, so a family there would be silently ignored."""
    catalog = {
        "models": {"chat": {"provider": "local", "model": "m", "family": "qwen"}},
        "default": "chat",
        "providers": {"local": {"base_url": LOCAL, "inference_provider": runtime}},
    }

    if accepted:
        _ = LlmSettings.model_validate(catalog)
    else:
        with pytest.raises(ValueError, match="ignores"):
            _ = LlmSettings.model_validate(catalog)


@pytest.mark.parametrize(
    ("toml", "env", "error"),
    [
        ('[llm]\nmodel = "gpt"\n', {}, r"llm\.model"),
        ("", {"HIVEGENT_SECURITY__USER_URLS": "[]"}, r"security\.user_urls"),
        ("", {"HIVEGENT_AUTH_ISSUER": "https://idp.test"}, "HIVEGENT_AUTH_ISSUER"),
    ],
    ids=["toml", "nested env", "flat env"],
)
def test_a_removed_setting_stops_the_boot_naming_it(
    load: Load, toml: str, env: dict[str, str], error: str
) -> None:
    """Unknown keys are refused in every section, from the TOML file and the environment."""
    with pytest.raises(ValueError, match=error):
        _ = load(toml, **env)


def test_a_named_config_file_must_exist(load: Load, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing.toml"):
        _ = load(**{CONFIG_FILE_ENV_VAR: str(tmp_path / "missing.toml")})


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
