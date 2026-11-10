"""Global maintenance mode: locks out non-admins instance-wide.

The authoritative flag is the ``maintenance_enabled`` column of the
:class:`~hivegent.db.models.ApplicationSettings` singleton; the value on
``app.state`` is a write-through cache seeded once at boot via
:func:`load_persisted_state` so the per-request gate never costs a
database round trip.  Toggles (``PUT /api/v1/admin/maintenance``) persist
the row first and update the cache second, so a failed write can never
leave memory ahead of disk.

The cache is only trustworthy in a single-process deployment: with
multiple workers, a toggle reaches one process while the others keep
serving their stale value until restart.  If that ever becomes the
deployment shape, replace the cache with per-request reads or a
short-TTL refresh.

While the flag is set, every API request from a non-admin fails with a
503 problem of type :data:`MAINTENANCE_PROBLEM`, which the frontend turns
into a full-screen maintenance notice.
Admins are exempt so they can keep working and switch the mode back
off.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Request, status

from ..auth import get_current_user
from ..db.application_settings import read_maintenance_enabled
from ..l10n import Localized
from ..store import User
from .problems import ProblemException

__all__ = [
    "MAINTENANCE_PROBLEM",
    "enforce_maintenance",
    "is_enabled",
    "load_persisted_state",
    "set_enabled",
]

MAINTENANCE_PROBLEM = "tag:dfki.de,2026:hivegent/maintenance"
"""The problem type a request refused for maintenance answers with."""

_MAINTENANCE_TITLE = Localized(en="Maintenance", de="Wartung")
_MAINTENANCE_DETAIL = Localized(
    en="The application is temporarily unavailable while an administrator "
    "performs maintenance. Please try again later.",
    de="Die Anwendung ist vorübergehend nicht verfügbar, während eine "
    "Administrator:in Wartungsarbeiten durchführt. Bitte versuchen Sie es später erneut.",
)


def is_enabled(app: FastAPI) -> bool:
    """Return whether maintenance mode is currently active for *app*."""
    return getattr(app.state, "maintenance_enabled", False)


def set_enabled(app: FastAPI, enabled: bool) -> None:
    """Update the in-memory cache of the maintenance flag.

    Callers that change the mode (rather than seed the cache) must
    persist via ``write_maintenance_enabled`` first — see the module
    docstring for the ordering rationale.
    """
    app.state.maintenance_enabled = enabled


async def load_persisted_state(app: FastAPI) -> None:
    """Seed the cache from the persisted singleton; called once at boot."""
    set_enabled(app, await read_maintenance_enabled())


async def enforce_maintenance(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """FastAPI dependency that rejects non-admin requests during maintenance.

    Mounted router-wide on the API router.  Admins pass through so
    they can operate the app and disable the mode again; everyone else
    (including PAT clients, which carry no roles) receives 503.

    Raises:
        ProblemException: 503 of type :data:`MAINTENANCE_PROBLEM` while
            the flag is set and the caller is not an admin.
    """
    if is_enabled(request.app) and not user.is_admin:
        raise ProblemException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            _MAINTENANCE_DETAIL.current,
            type=MAINTENANCE_PROBLEM,
            title=_MAINTENANCE_TITLE.current,
        )
