"""The conversation a caller owns and the shapes the queries return."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, field_validator
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

from ...config import sanitize_user_id

__all__ = [
    "ConversationData",
    "ConversationSummary",
    "OwnedConversation",
]


@dataclass(frozen=True, slots=True, init=False)
class OwnedConversation:
    """A conversation id together with the id of the user it belongs to.

    It has no public constructor: only :func:`owned_conversation` and its
    siblings build one, which read the owner from the database, or
    :func:`open_conversation` for an id no conversation holds, so holding
    one is the proof of ownership the store
    calls and the conversation's :meth:`folder` require.  An absent id proves
    no more than that it was free, so it is claimed (:func:`claim_conversation`)
    before anything is written.

    A conversation is the pair, never its id alone: every store call names the
    owner as its scope, its folder lies below the owner's, and the pair is what
    equality and hashing compare, so state kept per conversation is kept per
    owner by keying it by this value.  It names the owner without acting for
    them, since authority comes only from an authenticated :class:`User`, and
    a run checks that its conversation is its user's.  The id is a UUID, so
    its canonical string is a safe path segment and the store's id.
    """

    owner: str
    id: UUID

    @property
    def key(self) -> str:
        """The id the store keeps the conversation under."""
        return str(self.id)

    @staticmethod
    def root(data_dir: Path) -> Path:
        """Where every conversation's :meth:`folder` lives."""
        return data_dir / "conversations"

    @staticmethod
    def user_root(data_dir: Path, owner: str) -> Path:
        """Where the :meth:`folder` of every conversation *owner* owns lives."""
        return OwnedConversation.root(data_dir) / sanitize_user_id(owner)

    def folder(self, data_dir: Path) -> Path:
        """The one folder holding what this conversation keeps on disk, without creating it.

        Its ``/tmp`` and its staged changesets live below it, so deleting the
        conversation's files is deleting this folder.  It lies below its user's
        :meth:`user_root`, so a run still writing after its conversation was
        deleted never reaches another user who claims the freed id.
        """
        return self.user_root(data_dir, self.owner) / self.key


def _conversation(owner: str, conversation_id: UUID) -> OwnedConversation:
    """The one constructor of :class:`OwnedConversation`, for the ownership checks below."""
    conversation = object.__new__(OwnedConversation)
    object.__setattr__(conversation, "owner", owner)
    object.__setattr__(conversation, "id", conversation_id)

    return conversation


class ConversationData(BaseModel):
    """A conversation's active history with its summary, as the agent's tools read it."""

    id: UUID = Field(description="Conversation identifier")
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    messages: list[ModelMessage] = Field(default_factory=list)

    @field_validator("messages", mode="before")
    @classmethod
    def _validate_messages(cls, v: Any) -> list[ModelMessage]:
        return ModelMessagesTypeAdapter.validate_python(v)

    @field_serializer("messages")
    @classmethod
    def _serialize_messages(cls, v: list[ModelMessage]) -> Any:
        return ModelMessagesTypeAdapter.dump_python(v, mode="json")


class ConversationSummary(BaseModel):
    """Lightweight conversation row for list views."""

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
