"""Types every API model family shares: the agent modes and the progress sink of long operations."""

from typing import Literal, Protocol, get_args

__all__ = [
    "MAX_DETECTION_CHARS",
    "MODE_ORDER",
    "MODE_VALUES",
    "WRITABLE_MODES",
    "Mode",
    "ProgressReporter",
]


#: Upper bound on text submitted for watermark verification, roughly 250k
#: tokens at four characters per token.  That covers a generated document
#: whole, while keeping the body small enough for the upstream detector to
#: accept.
MAX_DETECTION_CHARS = 1_000_000


class ProgressReporter(Protocol):
    """Sink a long-running operation reports its progress to.

    Structural, so the workspace layer can report stages and counts without
    depending on the server's job framework: any object exposing these two
    methods (notably a job context) qualifies.
    """

    def set_stage(self, stage: str) -> None:
        """Set the current human-readable stage label."""
        ...

    def set_progress(self, current: int, total: int) -> None:
        """Set discrete progress, e.g. files processed so far."""
        ...


type Mode = Literal["interactive", "read", "write"]
"""Agent mode accepted from the API.

The mode selects which tools a run is offered and how their calls are gated
(``agents/policy.py``): ``interactive`` (the default) offers every effect and
asks the user to confirm every workspace write, ``write`` lands workspace
writes unattended, and ``read`` offers
only reads, its file writes narrowed to ``/tmp``.  A memory write and an
``execute`` tool ask in every mode, and ``/tmp`` never asks.
"""


MODE_VALUES: frozenset[Mode] = frozenset(get_args(Mode.__value__))
"""Valid mode strings, used to validate untrusted request input."""


WRITABLE_MODES: frozenset[Mode] = frozenset({"interactive", "write"})
"""Modes that offer tools of every effect and may write beyond ``/tmp``."""


MODE_ORDER: tuple[Mode, ...] = ("read", "interactive", "write")
"""The modes from the one that may do least to the one that asks least."""
