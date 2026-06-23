"""Generic background-job routes: list, live feed, and cancel.

Feature-agnostic surface over :mod:`hivegent.server.jobs`.  Any subsystem
that submits jobs (today: document processing) is observable and
cancellable through these endpoints; the client renders them uniformly
from the generic :class:`~hivegent.server.jobs.JobView` shape.
"""

import logging
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel

from ...auth import User, get_current_user
from ..jobs import JobView, manager

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


class CancelJobResponse(BaseModel):
    """Result of a cancel request: whether a live job was signalled."""

    job_id: str
    cancelled: bool


@router.get("/jobs")
async def list_jobs(
    user: Annotated[User, Depends(get_current_user)],
) -> list[JobView]:
    """List the caller's known jobs, oldest first."""
    return manager.list_jobs(user.id)


@router.get("/jobs/events", response_class=EventSourceResponse)
async def job_events(
    user: Annotated[User, Depends(get_current_user)],
) -> AsyncIterable[JobView]:
    """Stream the caller's job snapshots as Server-Sent Events.

    Emits the current snapshot of every job on connect, then one snapshot
    per state change.  Closing the stream only ends the subscription —
    the jobs themselves run independently and are unaffected.
    """
    async with manager.subscribe(user.id) as feed:
        async for snapshot in feed:
            yield snapshot


@router.delete("/jobs/{job_id}")
async def cancel_job(
    job_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> CancelJobResponse:
    """Request cancellation of one of the caller's jobs.

    Idempotent: cancelling an unknown or already-finished job reports
    ``cancelled=False`` rather than erroring, so a double click is safe.
    """
    return CancelJobResponse(job_id=job_id, cancelled=manager.cancel(user.id, job_id))
