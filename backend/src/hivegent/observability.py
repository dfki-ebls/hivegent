"""Local file-based observability using Logfire and OpenTelemetry."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import logfire
from fastapi import FastAPI
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from .config import settings

__all__ = ["configure_observability"]

logger = logging.getLogger(__name__)

# HTTP server spans for these methods carry no diagnostic value (CORS
# preflights, browser HEAD probes) and dominate volume in browser-driven
# traffic.  Dropped at export time so they never touch disk.
_NOISY_HTTP_METHODS = frozenset({"OPTIONS", "HEAD"})


def _is_noisy_span(span: ReadableSpan) -> bool:
    """Return ``True`` for HTTP server spans whose method is uninteresting."""
    attrs = span.attributes or {}
    method = attrs.get("http.method") or attrs.get("http.request.method")
    return isinstance(method, str) and method in _NOISY_HTTP_METHODS


@dataclass(slots=True, frozen=True)
class FileSpanExporter(SpanExporter):
    """Exports spans as JSON Lines to daily-rotated files.

    Each day's spans are appended to ``YYYY-MM-DD.jsonl`` inside
    *traces_dir*.  Noisy HTTP server spans (see :data:`_NOISY_HTTP_METHODS`)
    are dropped before writing.
    """

    traces_dir: Path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Write each kept span as a single JSON line to the daily trace file."""
        keep = [s for s in spans if not _is_noisy_span(s)]
        if not keep:
            return SpanExportResult.SUCCESS

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self.traces_dir / f"{today}.jsonl"

        try:
            self.traces_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for span in keep:
                    f.write(span.to_json(indent=None) + "\n")
        except OSError:
            logger.exception("Failed to write trace spans to %s", path)
            return SpanExportResult.FAILURE

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """No resources to release."""


def _prune_old_traces(traces_dir: Path, retention_days: int) -> None:
    """Delete ``YYYY-MM-DD.jsonl`` files older than *retention_days* days.

    Non-conforming filenames are left untouched.  ``retention_days <= 0``
    disables pruning.
    """
    if retention_days <= 0 or not traces_dir.is_dir():
        return

    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).date()
    for path in traces_dir.glob("????-??-??.jsonl"):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
            except OSError:
                logger.exception("Failed to delete old trace file %s", path)


def configure_observability(app: FastAPI) -> None:
    """Set up local file-based tracing via Logfire.

    Configures Logfire with ``send_to_logfire=False`` and attaches a
    :class:`FileSpanExporter` wrapped in :class:`BatchSpanProcessor` so
    span writes are buffered and flushed off the request thread.  Files
    older than ``settings.logfire.retention_days`` are pruned at startup.
    FastAPI, Pydantic AI, and MCP are instrumented automatically.

    Does nothing when ``settings.logfire.enable`` is ``False``.

    Args:
        app: The FastAPI application instance to instrument.
    """
    if not settings.logfire.enable:
        return

    traces_dir = settings.get_traces_dir()
    _prune_old_traces(traces_dir, settings.logfire.retention_days)

    exporter = FileSpanExporter(traces_dir)
    processor = BatchSpanProcessor(exporter)

    logfire.configure(
        send_to_logfire=False,
        additional_span_processors=[processor],
    )
    logfire.instrument_fastapi(app)
    logfire.instrument_pydantic_ai()
    logfire.instrument_mcp()

    logger.info("Observability configured — traces will be written to %s", traces_dir)
