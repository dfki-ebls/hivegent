"""Tests for the in-memory global maintenance gate."""

import pytest
from fastapi import FastAPI, HTTPException, Request

from hivegent.config import ADMIN_ROLE
from hivegent.server import maintenance
from hivegent.types import User


async def test_maintenance_gate_blocks_non_admins_only() -> None:
    """Off by default; when on, non-admins get 503 and admins pass."""
    app = FastAPI()
    request = Request({"type": "http", "app": app})
    user = User(id="u")
    admin = User(id="a", roles=frozenset({ADMIN_ROLE}))

    assert maintenance.is_enabled(app) is False
    await maintenance.enforce_maintenance(request, user)

    maintenance.set_enabled(app, True)
    with pytest.raises(HTTPException) as exc_info:
        await maintenance.enforce_maintenance(request, user)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == maintenance.MAINTENANCE_DETAIL
    await maintenance.enforce_maintenance(request, admin)

    maintenance.set_enabled(app, False)
    await maintenance.enforce_maintenance(request, user)
