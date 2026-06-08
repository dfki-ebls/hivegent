"""Generic scope contract for labeled search roots.

A :class:`Scope` labels a :class:`~hivegent.tools.base.SearchPath` so results
from different roots stay distinguishable and an incoming path can be routed
back to the root it names. The label grammar is the application's choice: this
module defines only the contract, never a concrete convention (no ``~`` / ``@``
or any other prefix lives here).
"""

from typing import Protocol

__all__ = ["Scope"]


class Scope(Protocol):
    """Renders local paths under a search root and routes qualified paths back.

    Implementations are the inverse of each other: :meth:`render` qualifies a
    local path, :meth:`strip` recovers the local path from a qualified one.
    """

    def render(self, local: str) -> str:
        """Return *local* rendered as a fully-qualified path under this scope."""
        ...

    def strip(self, raw: str) -> str | None:
        """Return *raw*'s local remainder if it addresses this scope, else ``None``.

        An empty string means *raw* names this scope's bare root; ``None`` means
        *raw* belongs to a different scope (or carries no scope at all).
        """
        ...
