"""What the host makes of what each tool declares, decided here and nowhere else.

Every tool declares its effect (`exar.tools.effect`), and every root
its :data:`~hivegent.tools.base.CommitPolicy`.  From those alone this module
derives the host's `exar.typing.Policy` (:func:`host_policy`), which denies the
tools a mode does not offer (:func:`offered`) or a run withholds and asks before
the calls that wait for the user, the policy of every exar run over a run's deps,
which ``guards.HOST_GATE`` applies to every tool of the run, which tools a model, an MCP client, and a program see
(:func:`listed`), and when a change to a root waits for the user (:func:`root_asks`).  What the MCP server
announces and what an external server's tool may do are exar's mapping between
effects and MCP annotations (`exar.mcp`).

A write through the changeset gateway is decided per item by the root it lands
in, which is why the tools carrying such writes are flagged
:attr:`ToolFlags.gateway`: the mode narrows their roots instead of withholding them, so
``read`` still writes ``/tmp``.  Every other write is :class:`Reviewed`, as if it
landed in a root of its own, and asks in every mode, as does every ``execute``
tool.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from exar.typing import Action, Decision, Effect, Policy
from pydantic import TypeAdapter

from ..config import settings
from ..tools.base import CommitPolicy, Direct, ModeGated, Reviewed
from ..types import WRITABLE_MODES, Mode

__all__ = [
    "HOST_FLAGS",
    "ToolFlags",
    "flags_of",
    "host_policy",
    "listed",
    "offered",
    "root_asks",
    "writable",
]

HOST_FLAGS = "host"
"""The tool metadata key of the :class:`ToolFlags` the host sets on every tool it serves as a plain mapping, which :func:`flags_of` reads back."""


@dataclass(frozen=True, slots=True)
class ToolFlags:
    """What the host knows of a tool beyond its effect, owned by :class:`~hivegent.agents.common.AgentTools`.

    Attributes:
        gateway: Whether every write of the tool lands through the changeset gateway.
        people_only: Whether the tool acts on one person's own data, which a service principal is never offered.
        derived: Whether the tool returns stored text derived from content beyond the workspaces.
            Past conversations and the memory may quote any document, so a run an
            assistant confines to an allowlist is never offered such a tool.
        internal: Whether the tool serves the host, which no run withholds.
        seeing: Whether the result of the tool is only of use to a model that sees images.
    """

    gateway: bool = False
    people_only: bool = False
    derived: bool = False
    internal: bool = False
    seeing: bool = False


_FLAGS = TypeAdapter(ToolFlags)


def flags_of(metadata: Mapping[str, object] | None, /) -> ToolFlags:
    """The flags a tool declares in its metadata, none for a tool the host did not build.

    >>> flags_of({HOST_FLAGS: {"gateway": True}}).gateway, flags_of(None).gateway
    (True, False)
    """
    return _FLAGS.validate_python((metadata or {}).get(HOST_FLAGS, {}))


def offered(effect: Effect, mode: Mode, *, gateway: bool) -> bool:
    """Whether *mode* offers a tool, from its effect.

    ``read`` offers only reads, and the gateway's writes, whose roots it
    narrows to ``/tmp`` instead.

    >>> offered("execute", "read", gateway=False), offered("write", "read", gateway=True)
    (False, True)
    """
    return effect == "read" or mode in WRITABLE_MODES or (gateway and effect == "write")


def writable(policy: CommitPolicy, mode: Mode) -> bool:
    """Whether a run in *mode* may change a root of *policy* at all.

    >>> writable(Direct(), "read"), writable(ModeGated(), "read")
    (True, False)
    """
    return isinstance(policy, Direct) or mode in WRITABLE_MODES


def root_asks(policy: CommitPolicy, mode: Mode) -> bool:
    """Whether a change to a root of *policy* waits for the user in *mode*.

    >>> root_asks(ModeGated(), "write"), root_asks(Reviewed(), "write")
    (False, True)
    """
    match policy:
        case Direct():
            return False

        case ModeGated():
            return mode != "write"

        case Reviewed():
            return True


def listed(policy: Policy, action: Action, /) -> bool:
    """Whether the model or an MCP client is shown the tool of *action*.

    What *policy* does not deny, but a ``settings.tools.sandbox_only`` tool,
    which only a program is handed.
    """
    return policy(action) != "deny" and action.name not in settings.tools.sandbox_only


def host_policy(
    mode: Mode,
    *,
    withheld: frozenset[str] = frozenset(),
    service: bool = False,
    confined: bool = False,
) -> Policy:
    """The host's policy on the tool calls of a run in *mode*, the policy of its exar run (``UserDeps.run_config``).

    It allows the agent invocations the host makes itself, such as summaries and completions.
    It denies every tool the run *withheld* by name, a people-only tool for a
    *service* principal, and a derived one for a *confined* run, unless the
    tool is internal.
    A mode denies every tool whose effect it does not offer (:func:`offered`).
    A write of a tool flagged :attr:`ToolFlags.gateway` is allowed here
    and asked about per item by the root it lands in (``agents/tools/write.py``),
    since only a call's arguments name the roots.  Any other write is
    :class:`Reviewed` and asks, as does every ``execute`` tool.

    >>> gateway = {HOST_FLAGS: {"gateway": True}}
    >>> host_policy("read")(Action(kind="tool", name="write_document", effect="write", metadata=gateway))
    'allow'
    >>> host_policy("read")(Action(kind="tool", name="write_memory", effect="write"))
    'deny'
    >>> host_policy("write")(Action(kind="tool", name="send_mail", effect="execute"))
    'ask'
    >>> host_policy("write", withheld=frozenset({"search"}))(Action(kind="tool", name="search", effect="read"))
    'deny'
    >>> host_policy("read")(Action(kind="agent", name="summarize", effect="execute"))
    'allow'
    """

    def decide(action: Action) -> Decision:
        if action.kind != "tool":
            return "allow"

        flags = flags_of(action.metadata)

        if not flags.internal and (
            action.name in withheld
            or (service and flags.people_only)
            or (confined and flags.derived)
        ):
            return "deny"

        gateway = action.effect == "write" and flags.gateway

        if not offered(action.effect, mode, gateway=gateway):
            return "deny"

        return "allow" if action.effect == "read" or gateway else "ask"

    return decide
