"""Centralized standard-library logging setup.

A single :func:`configure_logging` installs one root handler with a consistent
format and tames noisy third-party loggers.  A process enters through exactly
one of two entry points, so both call it: the CLI callback (every ``hivegent``
subcommand) and :func:`hivegent.server.app.create_app` (the ASGI factory, which
uvicorn runs in every worker, including ``--reload`` children that never touch
the CLI).

The ``serve`` command starts uvicorn with ``log_config=None``, so uvicorn
installs no handlers of its own and its ``uvicorn*`` loggers propagate to the
root handler installed here.  Every record (app, library, and uvicorn
access/error) then shares one format, with every code point escaped that
makes journalctl show the line as blob data, which also keeps a logged value
such as a file name from rewriting the line around it.
"""

import logging
import warnings

from .config import settings

__all__ = ["configure_logging"]


# Third-party loggers whose default level is too chatty.  Docling emits a line
# per pipeline stage per document; keep only its warnings, but let the pipeline
# logger through at INFO so long conversions still show progress.  Its Office
# backends nag per vector image (DrawingML, VML, EMF, WMF) to install
# LibreOffice, which the project does not ship, so they log errors only.
# sentence-transformers draws a tqdm bar per embedding or rerank call while its
# logger is at INFO, whose carriage returns journald shows as blob data.
_LIBRARY_LEVELS: dict[str, int] = {
    "sentence_transformers": logging.WARNING,
    "docling": logging.WARNING,
    "docling.pipeline": logging.INFO,
    "docling.backend.msword_backend": logging.ERROR,
    "docling.backend.mspowerpoint_backend": logging.ERROR,
    "docling.backend.msexcel_backend": logging.ERROR,
}

# joserfc warns on every EdDSA verification that RFC 9864 deprecates the generic
# ``EdDSA`` alg in favour of ``Ed25519``.  We only verify tokens, so the signing
# alg is the IdP's choice, not ours, and the warning is noise.  Installed once at
# import, before either entry point handles a request.
warnings.filterwarnings("ignore", message="EdDSA is deprecated via RFC 9864")

# The code points systemd's `utf8_is_printable` rejects: the C0 controls but
# tab and newline, DEL and the C1 controls (`unichar_is_control`), and the
# surrogates and noncharacters it takes for invalid UTF-8 (`unichar_is_valid`).
_REJECTED = (
    *(code for code in range(0x20) if chr(code) not in "\t\n"),
    *range(0x7F, 0xA0),
    *range(0xD800, 0xE000),
    *range(0xFDD0, 0xFDF0),
    *(plane | low for plane in range(0, 0x110000, 0x10000) for low in (0xFFFE, 0xFFFF)),
)
_ESCAPES: dict[int, str] = {
    code: chr(code).encode("unicode_escape").decode() for code in _REJECTED
}


class _PrintableFormatter(logging.Formatter):
    """A formatter whose lines journalctl prints rather than showing as blob data."""

    def format(self, record: logging.LogRecord) -> str:
        """Format *record* with the code points journalctl rejects escaped.

        >>> record = logging.makeLogRecord({"msg": "a\\rb\\x1b[0m\\ufffe ü"})
        >>> _PrintableFormatter().format(record)
        'a\\\\rb\\\\x1b[0m\\\\ufffe ü'
        """
        return super().format(record).translate(_ESCAPES)


def configure_logging() -> None:
    """Install the root log handler and quiet noisy libraries.

    Idempotent: :func:`logging.basicConfig` is a no-op once the root logger has
    a handler, so repeated calls (across CLI, factory, and reloads) never stack
    handlers.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        _PrintableFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
        )
    )
    logging.basicConfig(handlers=[handler], level=settings.logging.level)

    for name, level in _LIBRARY_LEVELS.items():
        logging.getLogger(name).setLevel(level)
