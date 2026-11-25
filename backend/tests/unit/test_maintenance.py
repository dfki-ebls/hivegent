"""Tests for the in-memory global maintenance gate."""

import pytest

from hivegent.config import ADMIN_ROLE
from hivegent.server import maintenance
from hivegent.server.problems import ProblemException
from hivegent.store import User


def test_maintenance_gate_blocks_non_admins_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off by default, when on, non-admins get 503 and admins pass."""
    monkeypatch.setattr(maintenance, "_flag", maintenance._Flag())
    user = User(id="u")
    admin = User(id="a", roles=frozenset({ADMIN_ROLE}))

    assert maintenance.is_enabled() is False
    maintenance.check_maintenance(user)

    maintenance.set_enabled(True)
    with pytest.raises(ProblemException) as exc_info:
        maintenance.check_maintenance(user)
    assert exc_info.value.status_code == 503
    assert exc_info.value.type == maintenance.MAINTENANCE_PROBLEM
    maintenance.check_maintenance(admin)
