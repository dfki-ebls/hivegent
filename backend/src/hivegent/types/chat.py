"""API models of chat turns, conversations, tools, and the chat debugger."""

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai.request_types import (
    RegenerateMessage,
    SubmitMessage,
    UIMessage,
)
from pydantic_ai_harness.sub_agents import DelegationOutcome

from ..config import settings
from ..config.llm import DEFAULT_REASONING_EFFORT
from ..db.conversations import ConversationSummary
from ..db.conversations.messages import is_user_request
from ..llm_config import ReasoningEffort
from ..shared.l10n import Localized
from .common import Mode
from .filters import FilterEntry

__all__ = [
    "DOCUMENTS_KEY",
    "AttachmentLimits",
    "ChatRequest",
    "ClientConversation",
    "ConversationArchive",
    "ConversationListResponse",
    "DebugChatRequest",
    "DebugReport",
    "DebugReportRequest",
    "DebugRequestConfig",
    "DocumentSelection",
    "GenerateTitleResponse",
    "InstructionsSnapshot",
    "ModelId",
    "Preferences",
    "ServerConversation",
    "ToolRunResult",
    "ToolSchema",
    "TranscriptionResponse",
    "UIChatRequest",
    "UpdateTitleRequest",
]


class DelegatedTask(BaseModel):
    """A task the main agent of a conversation delegated to a subagent, as the tasks panel lists it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_id: str | None = Field(
        description="The task that delegated it, none for one of the main agent"
    )
    agent_name: str = Field(description="The subagent it runs on")
    prompt: str = Field(description="The task as the subagent got it")
    status: Literal["running", "finished"]
    outcome: DelegationOutcome | None = Field(description="How it ended, once it did")
    output: str = Field(description="What it reported, once it ended")
    started_at: float = Field(description="When it started, as a UNIX time")
    finished_at: float | None = Field(description="When it ended, as a UNIX time")
    user_stopped: bool = Field(description="Whether the user stopped it")


class ToolSchema(BaseModel):
    """An agent tool with the JSON Schema of its call parameters, so a client can render a form to invoke it."""

    name: str
    description: str
    group: str
    parameters: dict[str, Any] = Field(
        description="JSON Schema of the tool's call parameters."
    )


class ToolRunResult(BaseModel):
    """Outcome of invoking an agent tool through the debug console.

    ``text`` and ``data`` are the two channels a tool result splits into,
    mirroring ``pydantic_ai.ToolReturn``: ``text`` is the
    compact string handed to the LLM (and MCP clients), while ``data`` is
    the structured payload the UI consumes from the data stream.
    """

    ok: bool
    text: str | None = Field(
        default=None,
        description="Compact text passed to the LLM (the tool's return value).",
    )
    data: Any = Field(
        default=None,
        description="Structured result consumed by the UI; null for text-only tools.",
    )
    error: str | None = None
    elapsed_ms: float


class UIChatRequest[MessageId: (str, UUID)](BaseModel):
    """The AI SDK's chat request body, which a chat route extends by its run configuration.

    The fields are the ones the SDK's ``DefaultChatTransport`` sends, and any
    other is refused, so a misspelled setting fails rather than going unread.
    A route whose messages carry the client's own ids takes them as strings.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="The AI SDK's chat id, the path names the conversation")
    messages: list[UIMessage] = Field(
        description="The messages the turn sends, see the route for which"
    )
    trigger: Literal["submit-message", "regenerate-message"] = Field(
        default="submit-message",
        description="Whether the turn submits a new or edited message or regenerates one",
    )
    message_id: MessageId | None = Field(
        default=None,
        alias="messageId",
        description="The message the turn edits (submit) or regenerates",
    )

    def run_input(self) -> SubmitMessage | RegenerateMessage:
        """The request the AI SDK adapter runs."""
        if self.trigger == "regenerate-message":
            return RegenerateMessage(
                trigger=self.trigger,
                id=self.id,
                messages=self.messages,
                message_id=None if self.message_id is None else str(self.message_id),
            )

        return SubmitMessage(id=self.id, messages=self.messages)


def _unknown_model(model_id: str) -> Localized[str]:
    return Localized(
        en=f"Unknown model: {model_id!r}", de=f"Unbekanntes Modell: „{model_id}“"
    )


def _check_model_id(model_id: str) -> str:
    """*model_id*, refused unless it names a chat model of the catalog."""
    model = settings.llm.models.get(model_id)
    if model is None or not model.chat:
        raise ValueError(_unknown_model(model_id).current)

    return model_id


type ModelId = Annotated[str, AfterValidator(_check_model_id)]
"""The id of a chat model in the operator's catalog."""


class DebugRequestConfig(BaseModel):
    """The run configuration of a chat debugger request."""

    model: ModelId | None = Field(
        default=None, description="Catalog id of the chat model, the default if null"
    )
    reasoning_effort: ReasoningEffort = Field(
        default=DEFAULT_REASONING_EFFORT,
        description="Reasoning effort level",
    )


class DebugReportRequest(DebugRequestConfig):
    """The chat debugger's session, to condense into a report."""

    messages: list[UIMessage]


class DebugChatRequest(DebugRequestConfig, UIChatRequest[str]):
    """A chat debugger turn, whose client sends the whole session."""


class DebugReport(BaseModel):
    """A chat debugger report, free of the analyzed conversation's data."""

    markdown: str
    masked: int = Field(
        description="Spans masked as <REDACTED_n> after the model failed to replace them.",
    )


MAX_INSTRUCTIONS_CHARS = 20_000
"""How long a user's own instructions may be."""

_STORED = "stored"
"""The validation context of stored preferences, which tolerates stale values."""


class Preferences(BaseModel):
    """The choices a user made for their runs, which every turn of theirs applies, whatever client sends it.

    The server keeps them, only the fields a user set, so a default the
    operator changes reaches everyone who never chose (:meth:`stored`).
    """

    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(
        default="",
        max_length=MAX_INSTRUCTIONS_CHARS,
        description="The user's own instructions, which every run follows at their authority",
    )
    mode: Mode = Field(
        default="interactive",
        description="Agent mode selecting the offered tools and how writes are gated",
    )
    model: ModelId | None = Field(
        default=None, description="Catalog id of the chat model, the default if null"
    )
    reasoning_effort: ReasoningEffort = Field(
        default=DEFAULT_REASONING_EFFORT,
        description="Reasoning effort level",
    )

    @field_validator("*", mode="wrap")
    @classmethod
    def _stale_at_default(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> object:
        """A stored value that no longer validates at its field's default, any other refused."""
        try:
            return handler(value)
        except ValidationError:
            if info.context != _STORED or info.field_name is None:
                raise

            return cls.model_fields[info.field_name].get_default(
                call_default_factory=True
            )

    @classmethod
    def stored(cls, values: Mapping[str, Any]) -> Self:
        """The preferences *values* stored, each one that no longer validates at its default.

        A stored choice outlives what it names, such as a model the catalog
        dropped or a mode that was renamed, which must not fail every turn.
        """
        known = {
            name: value for name, value in values.items() if name in cls.model_fields
        }

        return cls.model_validate(known, context=_STORED)


DOCUMENTS_KEY = "documents"
"""The key of a user message's metadata that holds the :class:`DocumentSelection` it is about."""


class DocumentSelection(BaseModel):
    """The documents a user message is about, which its metadata carries under :data:`DOCUMENTS_KEY`.

    It is stored with the message, so a regenerate, a resumed turn, and a
    compaction run with the selection of the question they answer.

    Args:
        included: What the agent is pointed at, which restricts nothing.
        excluded: What the agent's tools never reach.
    """

    model_config = ConfigDict(extra="forbid")

    included: list[FilterEntry] = Field(default_factory=list)
    excluded: list[FilterEntry] = Field(default_factory=list)

    @classmethod
    def from_metadata(cls, metadata: object) -> Self | None:
        """The selection a message's *metadata* names, None for none.

        Raises:
            ValidationError: When the selection it names does not validate.
        """
        selection = metadata.get(DOCUMENTS_KEY) if isinstance(metadata, dict) else None

        return None if selection is None else cls.model_validate(selection)

    @classmethod
    def of(cls, messages: Sequence[ModelMessage]) -> Self:
        """The selection of the latest user message of *messages*, an empty one if it carries none that validates."""
        prompt = next((m for m in reversed(messages) if is_user_request(m)), None)

        try:
            selection = None if prompt is None else cls.from_metadata(prompt.metadata)
        except ValidationError:
            selection = None

        return selection or cls()


class ChatRequest(UIChatRequest[UUID]):
    """A turn of a conversation: the AI SDK's body.

    The server owns the history, so ``messages`` holds only what is new: the
    user message a submit sends under a fresh UUID, its metadata naming the
    documents it is about (:class:`DocumentSelection`), none for a regenerate or a
    compaction, or, to resume a turn that paused, the assistant message
    carrying the user's approvals and answers.  Everything else a run applies
    is the user's :class:`Preferences`.
    """

    compact: bool = Field(
        default=False,
        description="Compact the active history instead of running a turn",
    )

    @model_validator(mode="after")
    def _check_selection(self) -> Self:
        for message in self.messages:
            if message.role == "user":
                _ = DocumentSelection.from_metadata(message.metadata)

        return self


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationSummary] = Field(
        description="List of conversation summaries",
    )


class InstructionsSnapshot(BaseModel):
    """One composed system prompt and the messages sent under it.

    Snapshots are deduplicated by text, so a conversation whose scope or mode
    changed mid-way carries one entry per distinct prompt rather
    than one per message, and *when* it changed is legible from the ids.
    """

    message_ids: list[str] = Field(
        description="UI ids of the messages sent under this prompt, in order"
    )
    text: str = Field(description="The fully composed system prompt")


class _ArchivedConversation(BaseModel):
    """Fields shared by both views of an archived conversation."""

    id: str | None = None
    title: str | None = None
    messages: list[UIMessage] = Field(default_factory=list)


class ServerConversation(_ArchivedConversation):
    """A persisted active history and the system prompts it ran under."""

    instructions: list[InstructionsSnapshot] = Field(default_factory=list)
    provenance: str | None = Field(
        default=None,
        description="Compact Ed25519-signed JWT marking the conversation as AI-generated",
    )


class ClientConversation(_ArchivedConversation):
    """A conversation exactly as the browser tab held it, errors included."""

    exported_at: str | None = None
    error: str | None = None


class ConversationArchive(BaseModel):
    """The interchange shape of ``GET`` and ``PUT /conversations/{id}/archive``.

    Both halves are optional and describe the same conversation from the two
    places it lives: ``backend`` is what was persisted (with the system prompts),
    ``frontend`` is what the user was looking at. A draft that never reached the
    server has only the latter; the API route, which has no browser state, emits
    only the former.

    Import restores ``backend`` when it carries messages and falls back to
    ``frontend``, so an archive of a conversation that failed before persisting
    still round-trips.
    """

    backend: ServerConversation | None = None
    frontend: ClientConversation | None = None

    def active_path(self) -> tuple[list[UIMessage], str | None]:
        """The messages to restore and the title to restore them under."""
        for half in (self.backend, self.frontend):
            if half is not None and half.messages:
                other = self.frontend if half is self.backend else self.backend

                return half.messages, half.title or (other.title if other else None)

        return [], None


class UpdateTitleRequest(BaseModel):
    """Request to update a conversation title."""

    title: str = Field(
        description="The new title for the conversation",
        max_length=200,
    )


class AttachmentLimits(BaseModel):
    """What a chat turn may attach, so the client can enforce it locally.

    The composer renders ``media_types`` as its file picker's ``accept``
    filter and checks ``max_bytes`` before upload, and the image cap of the
    selected model, ``ModelInfo.max_images``.  The chat route validates the
    same values, since that filter is a convenience and never the gate.
    """

    media_types: list[str] = Field(
        description="Media types the chat composer accepts, sorted"
    )
    max_bytes: int = Field(description="Size cap for a single attachment")


class GenerateTitleResponse(BaseModel):
    """Response from title generation."""

    title: str = Field(description="The generated title")


class TranscriptionResponse(BaseModel):
    """Response from audio transcription."""

    text: str = Field(description="The transcribed text")
