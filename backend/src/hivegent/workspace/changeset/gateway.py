"""The gateway bound to the roots a caller may change."""

from dataclasses import dataclass, replace

from ...tools.base import CommitPolicy
from ...tools.changes import Changeset
from ..operations import Root, policy_of, route
from .apply import AppliedChangeset, apply_changeset
from .resolve import PlannedChangeset, plan_changeset

__all__ = [
    "Gateway",
]


@dataclass(slots=True, frozen=True)
class Gateway:
    """The gateway bound to the roots a caller may change, each with what it may see of it.

    The one place a surface's canonical paths meet the gateway, so the agent,
    the MCP surface, and the host saving a tool result supply only their roots.

    Attributes:
        roots: The roots a canonical path may route to.
        host: Whether the host commits, see :func:`apply_changeset`.
    """

    roots: tuple[Root, ...]
    host: bool = False

    def policies(self, changeset: Changeset[str]) -> tuple[CommitPolicy, ...]:
        """The policy deciding the approval of each item of *changeset*, see :func:`policy_of`.

        Raises:
            ValueError: When a path names none of :attr:`roots`.
        """
        return tuple(map(policy_of, route(changeset, self.roots).operations))

    async def plan(self, changeset: Changeset[str]) -> PlannedChangeset:
        """Route and plan *changeset*, see :func:`plan_changeset`.

        Its summary carries the reasons of *changeset*, which routing leaves behind.

        Raises:
            ValueError: When a path names none of :attr:`roots`.
            ChangeRefused: When the gateway refuses the changeset.
        """
        routed = route(changeset, self.roots)
        planned = await plan_changeset(routed, host=self.host)
        summary = replace(planned.summary, reasons=changeset.reasons)

        return replace(planned, summary=summary)

    async def apply(self, changeset: Changeset[str]) -> AppliedChangeset:
        """Route and apply *changeset*, see :func:`apply_changeset`.

        Raises:
            ValueError: When a path names none of :attr:`roots`.
            ChangeRefused: When the gateway refuses the changeset.
        """
        return await apply_changeset(
            route(changeset, self.roots).operations, host=self.host
        )

    async def commit(self, changeset: Changeset[str]) -> str:
        """Apply *changeset* as :meth:`apply` does, its reports joined into one receipt."""
        return "\n".join((await self.apply(changeset)).reports)
