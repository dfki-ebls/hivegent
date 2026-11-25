"""The host policy derived from tool effects and root policies."""

from dataclasses import asdict

from exar.typing import Action

from hivegent.agents.capabilities import FEATURES
from hivegent.agents.policy import HOST_FLAGS, ToolFlags, host_policy, root_asks
from hivegent.store import GlobalScope, GroupScope, HostGrant, UserScope


def test_every_writable_workspace_lands_unattended_in_write_mode() -> None:
    """Own, group, and global workspaces ask only in interactive mode."""
    scopes = (UserScope("u"), GroupScope("team"), GlobalScope())
    policies = [HostGrant().workspace(scope).policy for scope in scopes]

    assert not any(root_asks(policy, "write") for policy in policies)
    assert all(root_asks(policy, "interactive") for policy in policies)


def test_read_mode_withholds_every_feature_with_a_write_outside_the_gateway() -> None:
    """Memory writes are withheld in read mode, while the gateway's writes stay for /tmp."""
    modes = {feature.id: feature.modes for feature in FEATURES}

    assert "read" not in modes["memory"]
    assert "read" in modes["write"]
    assert "read" in modes["python"]


def test_a_run_denies_what_it_withholds_unless_the_tool_is_internal() -> None:
    """The one rule of every surface, from the name and the flags of the action."""
    policy = host_policy(
        "write", withheld=frozenset({"a", "b"}), service=True, confined=True
    )

    def tool(name: str, **flags: bool) -> Action:
        return Action(
            kind="tool",
            name=name,
            effect="read",
            metadata={HOST_FLAGS: asdict(ToolFlags(**flags))},
        )

    assert policy(tool("a")) == "deny"
    assert policy(tool("b", internal=True)) == "allow"
    assert policy(tool("c", people_only=True)) == "deny"
    assert policy(tool("c", derived=True)) == "deny"
    assert host_policy("write")(tool("c", people_only=True, derived=True)) == "allow"
