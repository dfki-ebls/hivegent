"""Local file-based observability using Logfire and OpenTelemetry."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import logfire
from fastapi import FastAPI
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from .config import settings

__all__ = ["configure_observability"]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FileSpanExporter(SpanExporter):
    """Exports spans as JSON Lines to daily-rotated files.

    Each day's spans are appended to ``YYYY-MM-DD.jsonl`` inside
    *traces_dir*.
    """

    traces_dir: Path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Write each span as a single JSON line to the daily trace file."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self.traces_dir / f"{today}.jsonl"

        try:
            self.traces_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for span in spans:
                    f.write(span.to_json(indent=None) + "\n")
        except OSError:
            logger.exception("Failed to write trace spans to %s", path)
            return SpanExportResult.FAILURE

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """No resources to release."""


def configure_observability(app: FastAPI) -> None:
    """Set up local file-based tracing via Logfire.

    Configures Logfire with ``send_to_logfire=False`` and attaches a
    :class:`FileSpanExporter` that writes spans as JSON Lines to
    daily-rotated files under the configured traces directory.
    FastAPI, Pydantic AI, and MCP are instrumented automatically.

    Does nothing when ``settings.logfire.enabled`` is ``False``.

    Args:
        app: The FastAPI application instance to instrument.
    """
    if not settings.logfire.enabled:
        return

    traces_dir = settings.get_traces_dir()
    exporter = FileSpanExporter(traces_dir)

    logfire.configure(
        send_to_logfire=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
    )
    logfire.instrument_fastapi(app)
    logfire.instrument_pydantic_ai()
    logfire.instrument_mcp()

    logger.info("Observability configured — traces will be written to %s", traces_dir)
