"""Centralized standard-library logging setup.

A single :func:`configure_logging` installs one root handler with a consistent
format and tames noisy third-party loggers.  A process enters through exactly
one of two entry points, so both call it: the CLI callback (every ``hivegent``
subcommand) and :func:`hivegent.server.app.create_app` (the ASGI factory, which
uvicorn runs in every worker, including ``--reload`` children that never touch
the CLI).

The ``serve`` command starts uvicorn with ``log_config=None``, so uvicorn
installs no handlers of its own and its ``uvicorn*`` loggers propagate to the
root handler installed here.  Every record — app, library, and uvicorn
access/error — then shares one format.
"""

import logging
import warnings

from .config import settings

__all__ = ["configure_logging"]


# Third-party loggers whose default level is too chatty.  Docling emits a line
# per pipeline stage per document; keep only its warnings, but let the pipeline
# logger through at INFO so long conversions still show progress.
_LIBRARY_LEVELS: dict[str, int] = {
    "docling": logging.WARNING,
    "docling.pipeline": logging.INFO,
}

# joserfc warns on every EdDSA verification that RFC 9864 deprecates the generic
# ``EdDSA`` alg in favour of ``Ed25519``.  We only verify tokens, so the signing
# alg is the IdP's choice, not ours, and the warning is noise.  Installed once at
# import, before either entry point handles a request.
warnings.filterwarnings("ignore", message="EdDSA is deprecated via RFC 9864")


def configure_logging() -> None:
    """Install the root log handler and quiet noisy libraries.

    Idempotent: :func:`logging.basicConfig` is a no-op once the root logger has
    a handler, so repeated calls (across CLI, factory, and reloads) never stack
    handlers.
    """
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=settings.logging.level.upper(),
    )

    for name, level in _LIBRARY_LEVELS.items():
        logging.getLogger(name).setLevel(level)
