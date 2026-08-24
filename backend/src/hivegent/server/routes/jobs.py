"""Generic background-job routes: list, live feed, and cancel.

Feature-agnostic surface over :mod:`hivegent.jobs`.  Any subsystem
that submits jobs (today: document processing) is observable and
cancellable through these endpoints; the client renders them uniformly
from the generic :class:`~hivegent.jobs.JobView` shape.
"""

import logging
from collections.abc import AsyncIterable
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.sse import EventSourceResponse

from ...auth import User, get_current_user
from ...jobs import FeedEvent, JobView, manager
from ..common import ClientId

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/jobs")
async def list_jobs(
    user: Annotated[User, Depends(get_current_user)],
) -> list[JobView]:
    """List the caller's known jobs, oldest first."""
    return manager.list_jobs(user.id)


@router.get("/jobs/events", response_class=EventSourceResponse)
async def job_events(
    user: Annotated[User, Depends(get_current_user)],
    client: ClientId = None,
) -> AsyncIterable[FeedEvent]:
    """Stream the caller's job snapshots as Server-Sent Events.

    Emits the current snapshot of every job on connect, then a ``FeedReady``
    marker that ends the seed, then one snapshot per state change.  Closing
    the stream only ends the subscription — the jobs themselves run
    independently and are unaffected.
    """
    async with aclosing(manager.subscribe(user.id, client)) as feed:
        async for snapshot in feed:
            yield snapshot


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(
    job_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Request cancellation of one of the caller's jobs.

    Idempotent: unknown and already-finished jobs still return successfully.
    """
    manager.cancel(user.id, job_id)
