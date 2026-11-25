"""Routes for the assistants a user sees, saves, and deletes.

A user's and a group's assistants are written only here, a group's by its
writers, and the global ones come from the operator, so no route changes them.
"""

import asyncio

from fastapi import APIRouter, HTTPException, status

from ...agents.capabilities import unknown_tool_names
from ...assistants import (
    Assistant,
    delete_assistant,
    save_assistant,
    visible_assistants,
)
from ...auth import CurrentUser
from ...shared.l10n import Localized
from ...types import AssistantInfo, AssistantName, AssistantSpec

__all__ = ["router"]

router = APIRouter()


def _unknown_tools(names: str) -> Localized[str]:
    return Localized(
        en=f"The assistant withholds no such tool: {names}",
        de=f"Der Assistent schließt kein solches Tool aus: {names}",
    )


def _info(assistant: Assistant, *, writable: bool = True) -> AssistantInfo:
    return AssistantInfo(
        **assistant.spec.model_dump(),
        name=assistant.name,
        ref=assistant.ref,
        scope=assistant.scope.prefix,
        writable=writable,
    )


@router.get("/assistants")
async def list_assistants(user: CurrentUser) -> list[AssistantInfo]:
    """Every assistant the user sees: the global ones, each group's, and their own, in that order."""
    return [
        _info(entry.item, writable=entry.writable)
        for entry in await asyncio.to_thread(visible_assistants, user)
    ]


@router.put("/assistants/{scope}/{name}")
async def put_assistant(
    scope: str, name: AssistantName, spec: AssistantSpec, user: CurrentUser
) -> AssistantInfo:
    """Save the assistant *name* in *scope*, ``~`` or ``@<group>``, replacing one of its name."""
    if unknown := unknown_tool_names(spec.disabled_tools):
        raise HTTPException(
            status_code=422, detail=_unknown_tools(", ".join(unknown)).current
        )

    return _info(await save_assistant(user, scope, name, spec))


@router.delete("/assistants/{scope}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_assistant(scope: str, name: AssistantName, user: CurrentUser) -> None:
    """Delete the assistant *name* from *scope*."""
    await delete_assistant(user, scope, name)
