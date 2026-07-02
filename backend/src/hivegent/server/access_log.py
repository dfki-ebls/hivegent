"""Quieten uvicorn's access log for readiness-probe traffic."""

import logging

__all__ = ["install_probe_access_filter"]

# Full path of the readiness endpoint (the router carries a ``/api`` prefix).
_PROBE_PATH = "/api/health"


class _EndpointFilter(logging.Filter):
    """Drop access-log lines for a single request path.

    uvicorn logs each request via
    ``access_logger.info('%s - "%s %s HTTP/%s" %d', client, method, path, ...)``,
    so the request path is the third positional argument. Readiness probes
    (process-compose in dev, Caddy in prod) poll ``/api/health`` on a short
    interval, and those lines are pure noise.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args

        if isinstance(args, tuple) and len(args) >= 3:
            return args[2] != self._path

        return True


def install_probe_access_filter() -> None:
    """Silence ``/api/health`` in uvicorn's access log, idempotently.

    Called from :func:`hivegent.server.create_app`, which uvicorn imports and
    runs in every worker (including ``--reload`` children). A logger-level
    filter survives uvicorn's ``dictConfig`` because that only replaces
    handlers, never filters.
    """
    access_logger = logging.getLogger("uvicorn.access")

    if not any(isinstance(f, _EndpointFilter) for f in access_logger.filters):
        access_logger.addFilter(_EndpointFilter(_PROBE_PATH))
