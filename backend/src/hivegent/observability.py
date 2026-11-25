"""OpenTelemetry tracing into exar's traces, exported via Logfire when configured.

Every Pydantic AI agent is instrumented into exar's tracer provider
(`exar.pydantic_ai.instrumentation`), so the trace of every exar run holds its
model and tool spans.  It stays on without an exporter, since exar's
experience reads its trajectories from these spans, and spans live in memory
only while exar records a trace a caller asked for.  They carry no message
content unless :attr:`LogfireSettings.include_content` opts in, and never
binary content, so what an exporter receives is metadata by default.  When :attr:`LogfireSettings.otlp_endpoint` is set,
spans are also exported via OTLP/HTTP to a self-hosted backend (e.g. Grafana
Tempo on the same systemd host).  When :attr:`LogfireSettings.token` is set,
spans additionally go to Pydantic Logfire SaaS.  Logfire only adds exporters:
exar attaches its processor to the provider Logfire installs, so nothing is
instrumented twice.
"""

import logging
from typing import Any

import logfire
from exar.pydantic_ai import instrumentation
from fastapi import FastAPI, Request, WebSocket
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic_ai import Agent

from .config import settings
from .config.observability import LogfireSettings

__all__ = ["configure_observability"]

logger = logging.getLogger(__name__)


def configure_observability(app: FastAPI) -> None:
    """Instrument Pydantic AI into exar's traces, and export spans via Logfire when a destination is configured.

    Exports to :attr:`LogfireSettings.otlp_endpoint` over OTLP/HTTP and to
    Pydantic Logfire SaaS with :attr:`LogfireSettings.token`, either of which
    also instruments FastAPI, and with :attr:`LogfireSettings.include_content`
    only, the endpoints' arguments, MCP, and Monty, since they record whole
    payloads, chat bodies, the caller, tool arguments and results, executed
    code, and its inputs and outputs.

    Args:
        app: The FastAPI application instance to instrument.
    """
    if settings.logfire.enabled:
        _configure_logfire(app, settings.logfire)

    # After Logfire installed its provider, which exar then records from.
    Agent.instrument_all(
        instrumentation(
            include_content=settings.logfire.include_content,
            include_binary_content=False,
        )
    )


def _without_arguments(
    _request: Request | WebSocket, attributes: dict[str, Any]
) -> dict[str, Any]:
    """The span attributes of a request without its endpoint's arguments, its validation errors only by type and location."""
    errors = attributes.get("errors", [])

    return {
        "errors": [
            {"type": error.get("type"), "loc": error.get("loc")} for error in errors
        ]
    }


def _configure_logfire(app: FastAPI, config: LogfireSettings) -> None:
    endpoint = config.otlp_endpoint
    token = config.token
    processors = []

    if endpoint is not None:
        # The signal path below the base URL, as `OTEL_EXPORTER_OTLP_ENDPOINT` takes it.
        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        processors.append(BatchSpanProcessor(exporter))

    logfire.configure(
        service_name=config.service_name,
        token=None if token is None else token.get_secret_value(),
        send_to_logfire=token is not None,
        additional_span_processors=processors,
    )
    logfire.instrument_fastapi(
        app,
        request_attributes_mapper=None
        if config.include_content
        else _without_arguments,
    )

    if config.include_content:
        logfire.instrument_mcp()
        # Before the lifespan opens the sandbox pool, which it must precede.
        logfire.instrument_monty()

    logger.info(
        "Observability configured (otlp=%s, logfire_saas=%s)",
        endpoint or "off",
        token is not None,
    )
