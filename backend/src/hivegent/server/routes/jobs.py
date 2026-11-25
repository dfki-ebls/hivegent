"""Generic background-job routes: list, live feed, and cancel.

Feature-agnostic surface over :mod:`hivegent.jobs`.  Any subsystem
that submits jobs (today: document processing) is observable and
cancellable through these endpoints; the client renders them uniformly
from the generic :class:`~hivegent.jobs.JobView` shape.
"""

import logging
from collections.abc import AsyncIterable
from contextlib import aclosing

from fastapi import APIRouter, status
from fastapi.sse import EventSourceResponse

from ...auth import CurrentUser, until_expiry
from ...jobs import ADMINS, FeedEvent, JobView, manager
from ...store import User
from ...workspace_events import follows

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


def _owners(user: User) -> frozenset[str]:
    """Whose jobs *user* follows: their own, and an admin's also every admin's."""
    return frozenset({user.id, ADMINS} if user.is_admin else {user.id})


@router.get("/jobs")
async def list_jobs(
    user: CurrentUser,
) -> list[JobView]:
    """List the caller's known jobs, oldest first."""
    return manager.list_jobs(_owners(user))


@router.get("/jobs/events", response_class=EventSourceResponse)
async def job_events(
    user: CurrentUser,
) -> AsyncIterable[FeedEvent]:
    """Stream the caller's job snapshots as Server-Sent Events.

    Emits the current snapshot of every job on connect, then a ``FeedReady``
    marker that ends the seed, then one snapshot per state change, along with
    every change to a workspace the caller may read.  Closing the stream only
    ends the subscription, the jobs themselves run independently and are
    unaffected.  The stream ends when the caller's token expires, so a client
    reconnecting with a fresh one follows only what its grants still reach.
    """
    async with aclosing(manager.subscribe(_owners(user), follows(user))) as feed:
        async for snapshot in until_expiry(feed, user.expires_at):
            yield snapshot


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(
    job_id: str,
    user: CurrentUser,
) -> None:
    """Request cancellation of one of the caller's jobs, an admin's also every admin's.

    Idempotent: unknown and already-finished jobs still return successfully.
    """
    _ = manager.cancel(_owners(user), job_id)
