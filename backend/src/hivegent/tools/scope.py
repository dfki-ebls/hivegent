"""Generic scope contract for labeled search roots.

A :class:`Scope` labels a :class:`~hivegent.tools.base.SearchPath` so results
from different roots stay distinguishable and an incoming path can be routed
back to the root it names. The label grammar is the application's choice: this
module defines the contract and :class:`PrefixScope`, the one shape every
convention takes, while the prefixes themselves (``~``, ``@<group>``,
``/tmp``) are the application's.
"""

from dataclasses import dataclass
from typing import Protocol

__all__ = ["PrefixScope", "Scope"]


class Scope(Protocol):
    """Renders local paths under a search root and routes qualified paths back.

    Implementations are the inverse of each other: :meth:`render` qualifies a
    local path, :meth:`strip_prefix` recovers the local path from a qualified one.
    """

    def render(self, local: str) -> str:
        """Return *local* rendered as a fully-qualified path under this scope."""
        ...

    def strip_prefix(self, raw: str) -> str | None:
        """Return *raw*'s local remainder if it addresses this scope, else ``None``.

        An empty string means *raw* names this scope's bare root; ``None`` means
        *raw* belongs to a different scope (or carries no scope at all).
        """
        ...


@dataclass(slots=True, frozen=True)
class PrefixScope:
    """A scope whose paths all lead with one fixed *prefix*.

    >>> scope = PrefixScope("/tmp")
    >>> scope.render("run.py"), scope.render("")
    ('/tmp/run.py', '/tmp')
    >>> scope.strip_prefix("/tmp/run.py"), scope.strip_prefix("/tmp"), scope.strip_prefix("~/tmp/a")
    ('run.py', '', None)
    """

    prefix: str

    def render(self, local: str) -> str:
        """Render *local* under the prefix, the bare prefix for an empty one."""
        return f"{self.prefix}/{local}" if local else self.prefix

    def strip_prefix(self, raw: str) -> str | None:
        """Return *raw*'s local part if it leads with the prefix, else ``None``."""
        if raw == self.prefix:
            return ""

        tag = f"{self.prefix}/"

        return raw[len(tag) :] if raw.startswith(tag) else None
