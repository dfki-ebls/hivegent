"""Settings of logging, debugging, tracing, and transparency reporting."""

from typing import Annotated, Literal

from pydantic import Field, HttpUrl, SecretStr

from .common import HttpOperatorUrl, ProviderEndpoint, Section

__all__ = [
    "DebugSettings",
    "LogfireSettings",
    "LoggingSettings",
    "TransparencySettings",
]


class LoggingSettings(Section):
    """Standard-library logging settings.

    The root level applied by :func:`hivegent.logging_config.configure_logging`.
    Configurable via ``HIVEGENT_LOGGING__*`` environment variables.
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class DebugSettings(Section):
    """The admin debugging consoles.

    ``enable`` registers the tool and chat debugger routes, which do not exist
    otherwise.  Even then they serve admins only, and the chat debugger reads
    only the caller's own conversations.

    Configurable via ``HIVEGENT_DEBUG__*`` environment variables.
    """

    enable: bool = False


class LogfireSettings(Section):
    """OpenTelemetry tracing settings.

    Tracing is enabled exactly when ``otlp_endpoint`` or ``token`` is set.
    ``otlp_endpoint`` is the base URL of a self-hosted OTLP/HTTP backend such
    as Grafana Tempo, whose path the traces are posted below at
    ``v1/traces``, with or without a trailing slash.  ``token`` is a
    Pydantic Logfire write token, which sends them to Logfire SaaS and comes
    from the credential ``hivegent.LOGFIRE__TOKEN``.
    ``include_content`` records the prompts, completions, and tool arguments
    and results on the spans of Pydantic AI, and turns on the MCP and Monty
    instrumentation, which records whole payloads and executed code, all of
    which then reach every exporter, so it is off unless an operator opts in.
    Binary content, such as images, is never recorded.

    Configurable via ``HIVEGENT_LOGFIRE__*`` environment variables.
    """

    otlp_endpoint: HttpOperatorUrl | None = None
    token: SecretStr | None = None
    service_name: str = "hivegent"
    include_content: bool = False

    @property
    def enabled(self) -> bool:
        """Whether spans are exported, which an endpoint or a token turns on."""
        return self.otlp_endpoint is not None or self.token is not None


class TransparencySettings(ProviderEndpoint):
    """AI-generated text marking and detection settings.

    Configuring this section enables watermark detection and signing.
    The vLLM reference detector is attached to the inference server, so the
    provider serving the watermarked model also serves it, at
    ``detector_path``.  ``report_issuer`` is the public origin placed in
    signed reports.  ``secret_key`` is the root the signing key is derived
    from, so changing it rotates the key.

    Configurable via ``HIVEGENT_TRANSPARENCY__*`` environment variables.
    """

    detector_path: str = Field(min_length=1)
    """Resolved against the provider's ``base_url`` like a link, so an absolute
    path such as llmhop's ``/llmhop/proxy/<name>/detect`` lands on its origin."""
    report_issuer: HttpUrl
    secret_key: SecretStr = Field(min_length=32)
    contact_email: Annotated[str, Field(min_length=1)] | None = None
    #: Public ``x`` values of earlier signing keys, kept in the JWKS so reports
    #: and exports signed before a planned rotation still verify. Leave a
    #: compromised key out, since it could sign backdated reports.
    retired_public_keys: list[str] = []
