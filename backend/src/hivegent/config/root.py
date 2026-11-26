"""The root settings, layering init kwargs, env, credentials, TOML, and defaults."""

import os
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import (
    BaseSettings,
    NestedSecretsSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from .common import CatalogId, ProviderEndpoint, unknown_reference
from .compute import (
    ChunkingSettings,
    ComputeSettings,
    ConversionSettings,
    IsolationSettings,
    JobSettings,
)
from .infra import DatabaseSettings, NetworkSettings
from .integrations import (
    ApiSettings,
    McpServer,
    McpSettings,
    PluginSettings,
    RagoldSettings,
    SourceSettings,
    SqlDatabaseSettings,
)
from .limits import LimitsSettings, RateLimitSettings
from .llm import LlmSettings, MultimodalSettings, SubagentSettings
from .observability import (
    DebugSettings,
    LogfireSettings,
    LoggingSettings,
    TransparencySettings,
)
from .retrieval import (
    EmbeddingSettings,
    LocalEmbeddingSettings,
    RerankSettings,
)
from .security import AuthSettings, ClaimSettings, SecuritySettings
from .tools import (
    ConversationSettings,
    SandboxSettings,
    SkillSettings,
    TmpSettings,
    ToolsSettings,
)

__all__ = [
    "CONFIG_FILE_ENV_VAR",
    "DEFAULT_CONFIG_FILE",
    "Settings",
    "settings",
]

CONFIG_FILE_ENV_VAR = "HIVEGENT_CONFIG_FILE"

DEFAULT_CONFIG_FILE = Path("config.toml")

_ENV_PREFIX = "HIVEGENT_"


class Settings(BaseSettings):
    """Application settings resolved from init kwargs, env, credentials, TOML, defaults.

    See :meth:`settings_customise_sources` for the layered source order.
    """

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer credential files and a TOML config file underneath env vars.

        No ``.env`` file is read, since the working directory is the writable
        data directory in production, where a planted file would outrank the
        operator's configuration.  The TOML path is read from ``HIVEGENT_CONFIG_FILE`` so deployments can
        relocate it without code changes, and it must then exist, while a
        missing ``config.toml`` in the working directory is empty.
        Credential files come from ``$CREDENTIALS_DIRECTORY``, see the
        backend README's "Secrets are credential files".

        Raises:
            FileNotFoundError: When ``HIVEGENT_CONFIG_FILE`` names no file.
        """
        if (configured := os.environ.get(CONFIG_FILE_ENV_VAR)) is None:
            toml_path = DEFAULT_CONFIG_FILE
        elif not (toml_path := Path(configured)).is_file():
            raise FileNotFoundError(f"{CONFIG_FILE_ENV_VAR} names no file: {toml_path}")

        credentials = NestedSecretsSettingsSource(
            file_secret_settings,
            secrets_dir=os.environ.get("CREDENTIALS_DIRECTORY"),
            secrets_dir_missing="ok",
            secrets_prefix="hivegent.",
        )

        return (
            init_settings,
            env_settings,
            credentials,
            TomlConfigSettingsSource(settings_cls, toml_file=toml_path),
        )

    llm: LlmSettings = LlmSettings()
    subagents: SubagentSettings = SubagentSettings()
    multimodal: MultimodalSettings = MultimodalSettings()
    embedding: EmbeddingSettings = LocalEmbeddingSettings()
    rerank: RerankSettings | None = None
    logging: LoggingSettings = LoggingSettings()
    logfire: LogfireSettings = LogfireSettings()
    debug: DebugSettings = DebugSettings()
    plugins: PluginSettings = PluginSettings()
    transparency: TransparencySettings | None = None
    ragold: RagoldSettings = RagoldSettings()
    mcp: McpSettings = McpSettings()
    # The external MCP servers, keyed by the id that prefixes their tools.
    mcp_servers: dict[CatalogId, McpServer] = {}
    # The SQL databases and HTTP APIs offered as tools, whose ids prefix them like a server's.
    databases: dict[CatalogId, SqlDatabaseSettings] = {}
    apis: dict[CatalogId, ApiSettings] = {}
    # The external sources, keyed by the id that names their root `/mnt/<id>`.
    sources: dict[CatalogId, SourceSettings] = {}
    claims: ClaimSettings = ClaimSettings()
    auth: AuthSettings = AuthSettings()
    security: SecuritySettings = SecuritySettings()
    tools: ToolsSettings = ToolsSettings()
    sandbox: SandboxSettings = SandboxSettings()
    conversations: ConversationSettings = ConversationSettings()
    tmp: TmpSettings = TmpSettings()
    skills: SkillSettings = SkillSettings()
    compute: ComputeSettings = ComputeSettings()
    conversion: ConversionSettings = ConversionSettings()
    chunking: ChunkingSettings = ChunkingSettings()
    limits: LimitsSettings = LimitsSettings()
    rate_limits: RateLimitSettings = RateLimitSettings()
    network: NetworkSettings = NetworkSettings()
    jobs: JobSettings = JobSettings()
    isolation: IsolationSettings = IsolationSettings()
    db: DatabaseSettings = DatabaseSettings()

    data_dir: Path = Path("data")
    # Where the handbook is served, which the SPA links to, or none to hide the link.
    docs_url: str | None = None
    # The operator's explicit consent to the additional terms in `TERMS`,
    # without which the server refuses to start.
    accept_terms: bool = False

    @model_validator(mode="after")
    def _check_env(self) -> Self:
        """Refuse a ``HIVEGENT_*`` variable naming no section, as the sections refuse unknown keys."""
        fields = type(self).model_fields
        unknown = sorted(
            name
            for name in os.environ
            if name.startswith(_ENV_PREFIX)
            and name != CONFIG_FILE_ENV_VAR
            and name.removeprefix(_ENV_PREFIX).split("__")[0].lower() not in fields
        )

        if unknown:
            raise ValueError(
                f"Unknown settings in the environment: {', '.join(unknown)}"
            )

        return self

    @model_validator(mode="after")
    def _check_tool_ids(self) -> Self:
        """Refuse an id that prefixes the tools of two servers, databases, or APIs."""
        ids = [*self.mcp_servers, *self.databases, *self.apis]

        if taken := sorted({name for name in ids if ids.count(name) > 1}):
            raise ValueError(f"Tool prefixes are used twice: {', '.join(taken)}")

        return self

    @model_validator(mode="after")
    def _check_endpoints(self) -> Self:
        """Refuse a section naming an unknown catalog endpoint."""
        for section in type(self).model_fields:
            config = getattr(self, section)

            if (
                isinstance(config, ProviderEndpoint)
                and config.endpoint not in self.llm.providers
            ):
                raise unknown_reference("Section", section, "provider")

        return self


settings = Settings()
