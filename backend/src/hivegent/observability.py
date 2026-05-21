"""OpenTelemetry tracing via Logfire.

Tracing is opt-in.  When :attr:`LogfireSettings.otlp_endpoint` is set,
spans are exported via OTLP/HTTP to a self-hosted backend (e.g. Grafana
Tempo on the same systemd host).  When the ``LOGFIRE_TOKEN`` environment
variable is set, spans additionally go to Pydantic Logfire SaaS — useful
for local development.  When neither is configured, instrumentation is
skipped entirely so there is no runtime overhead.
"""

import logging
import os

import logfire
from fastapi import FastAPI

from .config import settings

__all__ = ["configure_observability"]

logger = logging.getLogger(__name__)


def configure_observability(app: FastAPI) -> None:
    """Set up tracing via Logfire when a destination is configured.

    Configures an OTLP/HTTP exporter pointed at
    :attr:`LogfireSettings.otlp_endpoint` (e.g. ``http://127.0.0.1:4318``)
    and enables Pydantic Logfire SaaS export when ``LOGFIRE_TOKEN`` is
    set.  FastAPI, Pydantic AI, and MCP are instrumented automatically.

    Does nothing when no destination is configured.

    Args:
        app: The FastAPI application instance to instrument.
    """
    endpoint = settings.logfire.otlp_endpoint
    has_token = bool(os.environ.get("LOGFIRE_TOKEN"))

    if not endpoint and not has_token:
        return

    extra_processors = []
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        extra_processors.append(BatchSpanProcessor(exporter))

    logfire.configure(
        service_name=settings.logfire.service_name,
        send_to_logfire="if-token-present",
        additional_span_processors=extra_processors,
    )
    logfire.instrument_fastapi(app)
    logfire.instrument_pydantic_ai()
    logfire.instrument_mcp()

    logger.info(
        "Observability configured (otlp=%s, logfire_saas=%s)",
        endpoint or "off",
        has_token,
    )
