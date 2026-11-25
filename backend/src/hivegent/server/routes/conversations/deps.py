"""What the conversation routes share, a path's conversation and a chat request's adapter."""

from functools import partial
from typing import Annotated

from fastapi import Depends
from pydantic_ai import DeferredToolRequests

from ....agents import UserDeps
from ....auth import CurrentPrincipal, CurrentUser
from ....db.conversations import (
    OwnedConversation,
    open_conversation,
    owned_conversation,
)
from ....shared.l10n import Localized
from ...common import found_by_id
from ...vercel import ChatAdapter

__all__ = ["TURN_RUNNING", "Adapter", "Opened", "Owned"]

type Adapter = ChatAdapter[UserDeps, str | DeferredToolRequests]
"""The adapter of a chat request, run by :data:`~hivegent.agents.chat_agent`."""


TURN_RUNNING = Localized(
    en="Another turn of this conversation is still running, wait for it or stop it",
    de="Eine andere Runde dieser Konversation läuft noch, warte auf sie oder stoppe sie",
)


async def _owned_conversation(
    conversation_id: str,
    user: CurrentUser,
) -> OwnedConversation:
    """The path's conversation, once the database names the caller its owner.

    Every ``{conversation_id}`` route takes the :class:`OwnedConversation` its
    message tree and ``/tmp`` need from here, and an id that is no UUID names
    no conversation.
    """
    return await found_by_id(conversation_id, partial(owned_conversation, user))


type Owned = Annotated[OwnedConversation, Depends(_owned_conversation)]


async def _opened_conversation(
    conversation_id: str,
    user: CurrentPrincipal,
) -> tuple[OwnedConversation, bool]:
    """The path's conversation and whether its row exists (:func:`open_conversation`)."""
    return await found_by_id(conversation_id, partial(open_conversation, user))


type Opened = Annotated[tuple[OwnedConversation, bool], Depends(_opened_conversation)]
