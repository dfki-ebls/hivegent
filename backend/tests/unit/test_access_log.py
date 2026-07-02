"""Tests for the uvicorn access-log probe filter."""

import logging
from collections.abc import Iterator

import pytest

from hivegent.server.access_log import _EndpointFilter, install_probe_access_filter


@pytest.fixture
def access_logger() -> Iterator[logging.Logger]:
    """Yield the uvicorn access logger, restoring its filters and level."""
    logger = logging.getLogger("uvicorn.access")
    filters, level = logger.filters[:], logger.level

    yield logger

    logger.filters[:] = filters
    logger.setLevel(level)


def _record(path: str) -> logging.LogRecord:
    """Build a record shaped like uvicorn's access log for *path*."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:0", "GET", path, "1.1", 200),
        exc_info=None,
    )


def _probe_filters(logger: logging.Logger) -> list[_EndpointFilter]:
    return [f for f in logger.filters if isinstance(f, _EndpointFilter)]


def test_probe_filter_drops_only_health(access_logger: logging.Logger) -> None:
    """`/api/health` is dropped; other paths (incl. `/api/healthz`) pass."""
    install_probe_access_filter()
    (probe_filter,) = _probe_filters(access_logger)

    assert probe_filter.filter(_record("/api/health")) is False
    assert probe_filter.filter(_record("/api/documents")) is True
    assert probe_filter.filter(_record("/api/healthz")) is True


def test_install_is_idempotent(access_logger: logging.Logger) -> None:
    """Repeated installation attaches the filter only once."""
    install_probe_access_filter()
    install_probe_access_filter()
    assert len(_probe_filters(access_logger)) == 1
