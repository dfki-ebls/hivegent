"""Routes listing, showing, and stopping the tasks a conversation delegated to subagents."""

from fastapi import APIRouter, HTTPException, status
from pydantic_ai.ui.vercel_ai.request_types import UIMessage

from ....delegations import delegations
from ....shared.l10n import Localized
from ....types import DelegatedTask
from ...vercel import dump_transcript
from .deps import Owned

__all__ = ["router"]

router = APIRouter()

_TASK_NOT_FOUND = Localized(
    en="The conversation has no such task", de="Die Konversation hat keine solche Aufgabe"
)


@router.get("/conversations/{conversation_id}/tasks")
async def list_conversation_tasks(conversation: Owned) -> list[DelegatedTask]:
    """List the tasks the conversation delegated, oldest first, running and ended alike.

    Each change to them reaches the owner's feed (``GET /jobs/events``) as a
    ``conversation-activity`` event of the kind ``tasks``.
    """
    tasks = await delegations.tasks(conversation)

    return [
        DelegatedTask.model_validate(task, from_attributes=True)
        for task in sorted(tasks, key=lambda task: task.started_at)
    ]


@router.get("/conversations/{conversation_id}/tasks/{task_id}/messages")
async def get_conversation_task_messages(
    conversation: Owned, task_id: str
) -> list[UIMessage]:
    """The messages of a task's subagent so far: the task it got, and its steps and report."""
    history = await delegations.history(conversation, task_id)

    if history is None:
        raise HTTPException(status_code=404, detail=_TASK_NOT_FOUND.current)

    return dump_transcript(history, None)


@router.delete(
    "/conversations/{conversation_id}/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def stop_conversation_task(conversation: Owned, task_id: str) -> None:
    """Stop a running task and the tasks it started, which the model then cannot resume on its own.

    Idempotent: an ended or unknown task stays as it is.
    """
    await delegations.cancel(conversation, task_id)
