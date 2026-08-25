"""Shared types and server-facing schemas for Hivegent."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Protocol, Self, get_args

from pydantic import BaseModel, Field, model_validator
from pydantic_ai.settings import ThinkingEffort
from pydantic_ai.ui.vercel_ai.request_types import UIMessage

from .chunkers import ChunkingSpec
from .config import ADMIN_ROLE
from .converters import ConversionSpec
from .db.conversations import ConversationSummary
from .entries import entry_owns, stem_path_from_reference
from .llm_config import LlmConfig, ReasoningEffort
from .prompts import Personality
from .security import (
    UnsafeUrlError,
    require_safe_url_shape,
    validate_external_headers,
)

__all__ = [
    "AUTO_APPROVED_MODES",
    "MODE_VALUES",
    "MUTATING_MODES",
    "REASONING_EFFORT_VALUES",
    "AdminFactoryResetResponse",
    "AdminGroupInfo",
    "AdminListGroupsResponse",
    "AdminListUsersResponse",
    "AdminMaintenanceState",
    "AdminReindexResponse",
    "AdminResetResponse",
    "AdminUserInfo",
    "AssetProcessingMode",
    "AttachmentLimits",
    "ChatRequestConfig",
    "ClientConversation",
    "CollectionCompleteEvent",
    "CollectionProgressEvent",
    "CompactConversationRequest",
    "CompactConversationResponse",
    "ConversationArchive",
    "ConversationListResponse",
    "CreateDirectoryRequest",
    "DeleteDirectoryRequest",
    "DirectoryEntry",
    "DirectoryTreeResponse",
    "DocumentFilter",
    "DocumentInfo",
    "DocumentLineCountsResponse",
    "FailedFile",
    "FrontendConfigResponse",
    "GenerateTitleRequest",
    "GenerateTitleResponse",
    "GroupInfo",
    "InstructionsSnapshot",
    "McpOAuth2Config",
    "McpServerConfig",
    "McpTestResponse",
    "Mode",
    "MoveDocumentRequest",
    "OidcPublicConfig",
    "PipelineSpec",
    "ProgressReporter",
    "ServerConversation",
    "SettingsResponse",
    "ToolInfo",
    "ToolRunResult",
    "ToolSchema",
    "ToolsSpec",
    "TranscriptionResponse",
    "UpdateTitleRequest",
    "UploadCompleteEvent",
    "UploadDocumentResponse",
    "User",
    "UserResponse",
    "WriteDocumentRequest",
]


class ProgressReporter(Protocol):
    """Sink a long-running operation reports its progress to.

    Structural, so the workspace layer can report stages and counts without
    depending on the server's job framework: any object exposing these two
    methods — notably a job context — qualifies.
    """

    def set_stage(self, stage: str) -> None:
        """Set the current human-readable stage label."""
        ...

    def set_progress(self, current: int, total: int) -> None:
        """Set discrete progress, e.g. files processed so far."""
        ...


@dataclass(slots=True, frozen=True)
class DocumentFilter:
    """Include and exclude filter applied to document-level operations.

    Entries use the workspace UI's conventions: ``dir/`` (trailing slash)
    selects a directory subtree including the directory itself, ``/``
    selects the whole store, and a file entry selects the logical document
    — the file plus its same-stem siblings (description and original) and
    its ``.assets`` payload, mirroring how the inventory groups files into
    entries.

    When both an included and an excluded entry select a path, the most
    specific entry (the longest one) wins, with include winning ties — so
    a child document can be re-included from an excluded directory and a
    file can be excluded from an included directory.  Ancestor
    directories of an included entry count as include matches so listings
    can traverse into included subtrees.  Without any matching entry a
    path passes unless an include list is set: ``included=None`` means no
    include restriction, while an empty set hides the whole store — the
    store was not part of a non-empty whitelist.
    """

    included: frozenset[str] | None = None
    excluded: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def _selects(entry: str, path: str) -> bool:
        """Whether *entry* selects the file or directory *path*."""
        if entry.endswith("/"):
            return entry == "/" or path == entry[:-1] or path.startswith(entry)
        if path == entry:
            return True
        return entry_owns(
            stem_path_from_reference(entry), stem_path_from_reference(path)
        )

    def __call__(self, path: str) -> bool:
        """Return whether the file or directory *path* passes the filter."""
        include_match = max(
            (
                len(entry)
                for entry in self.included or ()
                if self._selects(entry, path) or entry.startswith(f"{path}/")
            ),
            default=-1,
        )
        exclude_match = max(
            (len(entry) for entry in self.excluded if self._selects(entry, path)),
            default=-1,
        )
        if include_match >= 0 and include_match >= exclude_match:
            return True
        if exclude_match >= 0:
            return False
        return self.included is None


@dataclass(slots=True, frozen=True)
class User:
    """Authenticated user with group membership and roles.

    Groups model shared knowledge containers (read/write per group);
    roles model global capabilities.  Admin status is derived on every
    access from the fixed :data:`~hivegent.config.ADMIN_ROLE` being
    present in :attr:`roles` — no duplicate flag is stored anywhere.
    Grant admin by adding a user to that role in the IdP (or, for dev,
    auth is disabled and the local user is admin).

    Group membership lives solely in the OIDC token and is never
    persisted: every field here is reconstructed per request from the
    validated JWT claims.

    Groups are held as IDs, which is what every path, workspace directory,
    and owner column uses.  ``group_labels`` carries the display names a
    provider additionally supplied, for the client to show in place of an
    opaque ID; nothing on the server reads it, so a rename upstream changes
    only what the UI renders.
    """

    id: str
    email: str | None = None
    name: str | None = None
    read_groups: frozenset[str] = field(default_factory=frozenset)
    write_groups: frozenset[str] = field(default_factory=frozenset)
    group_labels: Mapping[str, str] = field(default_factory=dict, compare=False)
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_groups(self) -> frozenset[str]:
        """Return all groups the user belongs to."""
        return self.read_groups | self.write_groups

    @property
    def is_admin(self) -> bool:
        """Whether the user holds the fixed admin role."""
        return ADMIN_ROLE in self.roles


class AssetProcessingMode(str, Enum):
    """How extracted assets (images, etc.) are handled during ingestion.

    ``ignore`` drops the assets entirely and strips their markdown
    references; ``store`` persists the raw bytes alongside the document
    without describing them; ``describe`` runs the asset-triage pipeline
    and describes the assets it deems semantically meaningful.
    """

    IGNORE = "ignore"
    STORE = "store"
    DESCRIBE = "describe"


class PipelineSpec(BaseModel):
    """Bundled conversion and chunking pipeline selection."""

    conversion: ConversionSpec = Field(default_factory=ConversionSpec)
    chunking: ChunkingSpec = Field(default_factory=ChunkingSpec)
    process_assets: AssetProcessingMode = AssetProcessingMode.STORE


class McpOAuth2Config(BaseModel):
    """OAuth2 Client Credentials configuration for an MCP server."""

    client_id: str
    client_secret: str
    scopes: str | None = None


class McpServerConfig(BaseModel):
    """User-provided MCP server configuration.

    ``url`` and ``headers`` run through the URL policy and header-injection
    checks, and every connection goes through the SSRF-safe egress proxy.
    """

    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    tool_prefix: str | None = None
    oauth2: McpOAuth2Config | None = None

    @model_validator(mode="after")
    def _check_safety(self) -> Self:
        require_safe_url_shape(self.url, "MCP server url")
        try:
            validate_external_headers(self.headers)
        except UnsafeUrlError as exc:
            raise ValueError(f"Unsafe MCP server headers: {exc}") from exc
        return self


class ToolsSpec(BaseModel):
    """Built-in tool configuration and custom MCP servers."""

    disabled_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)


class ToolInfo(BaseModel):
    """Metadata about an available agent tool."""

    name: str
    description: str
    group: str


class ToolSchema(ToolInfo):
    """Tool metadata plus the JSON Schema of its call parameters.

    Extends :class:`ToolInfo` with the full parameter schema so a client
    can render a form to invoke the tool generically.
    """

    parameters: dict[str, Any] = Field(
        description="JSON Schema of the tool's call parameters."
    )


class ToolRunResult(BaseModel):
    """Outcome of invoking an agent tool through the debug console.

    ``text`` and ``data`` are the two channels a tool result splits into,
    mirroring :class:`~hivegent.tools.base.ToolOutput`: ``text`` is the
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


REASONING_EFFORT_VALUES: frozenset[str] = frozenset(
    {"auto", "none", *get_args(ThinkingEffort)}
)
"""Valid reasoning effort strings, used to validate untrusted request input."""


type Mode = Literal["interactive", "read", "write", "plan"]
"""Agent mode accepted from the API.

The mode selects which features a run is composed from and how their tools are
gated: ``interactive`` (the default) offers the mutating features and asks the
user to confirm every document write, ``write`` runs them unattended, and
``read`` withholds them entirely.  ``plan`` is ``read`` plus the planning tool
and its instructions, so the agent drafts a plan for the user to approve
instead of acting.
"""

MODE_VALUES: frozenset[Mode] = frozenset(get_args(Mode.__value__))
"""Valid mode strings, used to validate untrusted request input."""

MUTATING_MODES: frozenset[Mode] = frozenset({"interactive", "write"})
"""Modes in which workspace mutations are available."""

AUTO_APPROVED_MODES: frozenset[Mode] = frozenset({"write"})
"""Modes that run a state-changing tool without pausing for confirmation.

The one table behind both halves of the gate: the capability that downgrades
every tool registered as approval-requiring, and a tool that decides per call
whether this one changes state.  Two literals would let a new mode lift the gate
for one and not the other.
"""


class ChatRequestConfig(BaseModel):
    """Configuration for chat requests, passed via request body."""

    conversation_id: str = Field(default="", description="The conversation ID")
    personality: Personality = Field(default=Personality.DEFAULT)
    system_message: str = Field(
        default="",
        description="Custom system message (used when personality is 'custom')",
    )
    reasoning_effort: ReasoningEffort = Field(
        default="auto",
        description="Reasoning effort level ('auto' resolves to the deployed default effort, 'none' disables thinking)",
    )
    mode: Mode = Field(
        default="interactive",
        description="Agent mode selecting the offered tools and how writes are gated",
    )
    llm: LlmConfig = Field(default_factory=LlmConfig)
    included_documents: list[str] = Field(default_factory=list)
    excluded_documents: list[str] = Field(default_factory=list)
    tools: ToolsSpec = Field(default_factory=ToolsSpec)
    trigger: Literal["submit-message", "regenerate-message"] = Field(
        default="submit-message",
        description="Whether this turn submits a new/edited message or regenerates one",
    )
    message_id: str | None = Field(
        default=None,
        description="Target node id: the edited message (submit) or the regenerated one",
    )


class DocumentInfo(BaseModel):
    """Metadata about a logical workspace entry."""

    filename: str = Field(description="The filename of the document")
    display_name: str = Field(description="User-facing basename of the logical entry")
    size_bytes: int = Field(description="File size in bytes")
    modified_at: datetime = Field(description="Last modification timestamp")
    chunk_count: int | None = Field(
        default=None,
        description="Number of chunks, if chunked",
    )
    has_original: bool = Field(
        default=False,
        description="Whether an original binary file exists for reconversion",
    )
    original_path: str | None = Field(
        default=None,
        description="Workspace-relative original file path when present",
    )
    assets_dir: str | None = Field(
        default=None,
        description="Workspace-relative child-assets directory when present",
    )
    kind: Literal["document", "asset"] = Field(
        default="document",
        description="Whether this is a logical document entry or raw asset row",
    )


class AssetEntry(BaseModel):
    """Information about a single file in a document's assets directory."""

    name: str = Field(description="Path relative to the assets directory")
    path: str = Field(description="Workspace-relative path to the asset file")
    description_path: str | None = Field(
        default=None,
        description="Workspace-relative path to the companion .md description file",
    )
    description: str = Field(
        default="",
        description="Content of the companion .md file",
    )
    size_bytes: int = Field(description="File size in bytes")
    media_type: str | None = Field(
        default=None,
        description="MIME type of the asset file",
    )


class AssetListResponse(BaseModel):
    """Response for listing a document's assets."""

    assets: list[AssetEntry] = Field(description="Asset files in the directory")
    assets_dir: str = Field(description="Workspace-relative assets directory path")


class UpdateAssetDescriptionRequest(BaseModel):
    """Request to update an asset's companion .md description."""

    asset_name: str = Field(description="Path of the asset within its assets directory")
    content: str = Field(description="New content for the .md description file")


class GenerateAssetDescriptionRequest(BaseModel):
    """Request to generate an asset's companion .md description with the vision model."""

    asset_name: str = Field(description="Path of the asset within its assets directory")
    llm: LlmConfig = Field(default_factory=LlmConfig)


class UploadDocumentResponse(BaseModel):
    """Response for document upload."""

    filename: str = Field(description="The uploaded filename")
    converted_filename: str | None = Field(
        default=None,
        description="The converted markdown filename (for binary files)",
    )
    size_bytes: int = Field(description="File size in bytes")
    conversion_pipeline_used: str | None = Field(
        default=None,
        description="The conversion pipeline used (for binary files)",
    )
    chunk_count: int | None = Field(
        default=None,
        description="Number of chunks created",
    )
    chunking_pipeline_used: str | None = Field(
        default=None,
        description="The chunking pipeline used",
    )
    message: str = Field(description="Status message")


class DocumentLineCountsResponse(BaseModel):
    """Batch document line counts, keyed by the requested workspace path."""

    line_counts: dict[str, int] = Field(
        description="Requested path → line count; unknown paths are omitted"
    )


class WriteDocumentRequest(BaseModel):
    """Request to write a text document, replacing it or creating it fresh."""

    content: str = Field(description="New full content for the document")
    mode: Literal["replace", "create"] = Field(
        default="replace",
        description="'replace' overwrites or creates; 'create' rejects an existing path",
    )
    chunking: ChunkingSpec | None = Field(
        default=None,
        description="Chunking pipeline to re-index with (defaults to the standard pipeline)",
    )


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationSummary] = Field(
        description="List of conversation summaries",
    )
    total_count: int = Field(description="Total number of conversations")


class CompactConversationRequest(BaseModel):
    """Request to compact a conversation.

    The client sends its in-memory message history so the summary reflects
    the exact branch and partial turn visible when the user requests
    compaction.
    """

    llm: LlmConfig = Field(default_factory=LlmConfig)
    messages: list[UIMessage] = Field(default_factory=list)


class InstructionsSnapshot(BaseModel):
    """One composed system prompt and the messages sent under it.

    Snapshots are deduplicated by text, so a conversation whose scope, mode, or
    personality changed mid-way carries one entry per distinct prompt rather
    than one per message, and *when* it changed is legible from the ids.
    """

    message_ids: list[str] = Field(
        description="Ids of the messages sent under this prompt, in order"
    )
    text: str = Field(description="The fully composed system prompt")


class _ArchivedConversation(BaseModel):
    """Fields shared by both views of an archived conversation."""

    id: str | None = None
    title: str | None = None
    messages: list[UIMessage] = Field(default_factory=list)


class ServerConversation(_ArchivedConversation):
    """A persisted active path and the system prompts it ran under."""

    instructions: list[InstructionsSnapshot] = Field(default_factory=list)


class ClientConversation(_ArchivedConversation):
    """A conversation exactly as the browser tab held it, errors included."""

    exported_at: str | None = None
    error: str | None = None


class ConversationArchive(BaseModel):
    """The interchange shape of the ``/export`` and ``/import`` routes.

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


class CompactConversationResponse(BaseModel):
    """Response for conversation compaction."""

    new_conversation_id: str = Field(
        description="ID of the new compacted conversation",
    )
    summary: str = Field(description="Summary of the original conversation")
    message: str = Field(description="Status message")


class UpdateTitleRequest(BaseModel):
    """Request to update a conversation title."""

    title: str = Field(
        description="The new title for the conversation",
        max_length=200,
    )


class GroupInfo(BaseModel):
    """One group a user belongs to, with the permission they hold on it.

    The ID is what every path and request uses; ``name`` is a label only,
    falling back to the ID when the provider supplied no display name.  One
    entry per group rather than a read list beside a write list, so a client
    can render the set directly without merging two overlapping ones.
    """

    id: str = Field(description="Group identifier, used to build paths")
    name: str = Field(description="Display label; the ID when none was supplied")
    writable: bool = Field(description="Whether the user may write to the group")

    @staticmethod
    def build(user: User) -> list["GroupInfo"]:
        """Serialize a user's groups, ordered as a client lists them."""
        return sorted(
            (
                GroupInfo(
                    id=group_id,
                    name=user.group_labels.get(group_id, group_id),
                    writable=group_id in user.write_groups,
                )
                for group_id in user.all_groups
            ),
            key=lambda group: group.name,
        )


class UserResponse(BaseModel):
    """Serializable user information for API responses.

    Admin status is intentionally not on the wire — the client derives
    it the same way the server does, by checking for the fixed ``admin``
    role in ``roles``.
    """

    id: str = Field(description="User identifier")
    email: str | None = Field(default=None, description="User email address")
    name: str | None = Field(default=None, description="User display name")
    groups: list[GroupInfo] = Field(
        default_factory=list,
        description="Groups the user belongs to, with their permission on each",
    )
    roles: list[str] = Field(
        default_factory=list,
        description="Global roles held by the user",
    )

    @staticmethod
    def from_user(user: User) -> "UserResponse":
        """Create a serializable response from an authenticated user."""

        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            groups=GroupInfo.build(user),
            roles=sorted(user.roles),
        )


class AttachmentLimits(BaseModel):
    """What a chat turn may attach, so the client can enforce it locally.

    The composer renders ``media_types`` as its file picker's ``accept``
    filter and checks ``max_bytes`` before upload.  The chat route
    validates the same values, since that filter is a convenience and
    never the gate.
    """

    media_types: list[str] = Field(
        description="Media types the chat composer accepts, sorted"
    )
    max_bytes: int = Field(description="Size cap for a single attachment")


class SettingsResponse(BaseModel):
    """LLM settings with user context."""

    model: str = Field(description="Default chat model")
    aux_model: str | None = Field(
        description=(
            "Default auxiliary model — must be small, fast, and vision-capable. "
            "Used for document conversion, alt text, titles, compaction, and chunking."
        ),
    )
    stt_model: str | None = Field(
        description=(
            "Audio transcription model backing the speech input fallback; "
            "None when transcription is not configured."
        ),
    )
    has_api_key: bool = Field(description="Whether a server-side API key is configured")
    base_url: str = Field(description="Default base URL for the LLM provider")
    user: UserResponse = Field(description="Authenticated user information")
    attachments: AttachmentLimits = Field(
        description="Constraints the chat composer enforces on attachments"
    )


class OidcPublicConfig(BaseModel):
    """Public OIDC parameters the SPA needs to start a login flow."""

    issuer_uri: str = Field(description="OIDC issuer the SPA authenticates against")
    client_id: str = Field(description="Public OIDC client id the SPA registers as")


class FrontendConfigResponse(BaseModel):
    """Runtime configuration the browser SPA fetches at startup.

    Served unauthenticated and carrying only public values, this is the
    frontend's single source of truth — it reads its OIDC config from here
    instead of baking it in at build time.
    """

    oidc: OidcPublicConfig = Field(description="OIDC client configuration")


class GenerateTitleRequest(BaseModel):
    """Request to generate a title using an LLM."""

    llm: LlmConfig = Field(default_factory=LlmConfig)


class GenerateTitleResponse(BaseModel):
    """Response from title generation."""

    title: str = Field(description="The generated title")


class TranscriptionResponse(BaseModel):
    """Response from audio transcription."""

    text: str = Field(description="The transcribed text")


class DirectoryEntry(BaseModel):
    """A logical file or directory entry in the document tree."""

    type: Literal["file", "directory"]
    name: str = Field(description="Basename of the entry")
    path: str = Field(description="Relative path from documents root")
    size_bytes: int | None = Field(default=None, description="File size in bytes")
    modified_at: datetime | None = Field(
        default=None,
        description="Last modification timestamp",
    )
    chunk_count: int | None = Field(
        default=None,
        description="Number of chunks, if chunked",
    )
    has_original: bool = Field(
        default=False,
        description="Whether an original binary file exists",
    )
    original_path: str | None = Field(
        default=None,
        description="Workspace-relative original file path when present",
    )
    assets_dir: str | None = Field(
        default=None,
        description="Workspace-relative child-assets directory when present",
    )
    children: list["DirectoryEntry"] | None = Field(
        default=None,
        description="Child entries for directories",
    )


class DirectoryTreeResponse(BaseModel):
    """Response for the directory tree endpoint."""

    root: DirectoryEntry
    total_files: int = Field(description="Total number of files")
    total_directories: int = Field(description="Total number of directories")


class CreateDirectoryRequest(BaseModel):
    """Request to create a new directory."""

    path: str = Field(description="Relative path of the directory to create")


class MoveDocumentRequest(BaseModel):
    """Request to move a document to a new location."""

    destination: str = Field(description="Destination relative path")


class MoveDirectoryRequest(BaseModel):
    """Request to move/rename a directory."""

    source: str = Field(description="Current relative path of the directory")
    destination: str = Field(description="New relative path for the directory")


class DeleteDirectoryRequest(BaseModel):
    """Request to delete a directory."""

    path: str = Field(description="Relative path of the directory to delete")


class FailedFile(BaseModel):
    """A collection member that was skipped or failed, with a short reason."""

    path: str = Field(description="Workspace-relative path of the file")
    reason: str = Field(description="Why the file was not imported")


class CollectionProgressEvent(BaseModel):
    """Progress event yielded per file while a collection job runs."""

    current: int = Field(description="Number of files processed so far")
    total: int = Field(description="Total number of files to process")


class CollectionCompleteEvent(BaseModel):
    """Terminal event summarizing a finished collection import."""

    markdown_files: int = Field(description="Number of markdown files uploaded")
    converted_attachments: int = Field(
        description="Number of binary attachments converted",
    )
    failed_files: list[FailedFile] = Field(
        default_factory=list,
        description="Files that were skipped or failed, each with its reason",
    )
    message: str = Field(description="Status message")


class UploadCompleteEvent(UploadDocumentResponse):
    """Completion payload for upload, reconvert, and replace operations."""

    type: Literal["complete"] = "complete"


class McpTestResponse(BaseModel):
    """Response from testing an MCP server connection."""

    ok: bool = Field(description="Whether the connection test succeeded")
    tool_count: int | None = Field(
        default=None,
        description="Number of tools discovered",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the test failed",
    )


# ─── Admin responses ───────────────────────────────────────────────────


class AdminResetResponse(BaseModel):
    """Generic response for a single admin reset action."""

    action: str = Field(description="The action that was performed")
    message: str = Field(description="Human-readable status message")


class AdminReindexResponse(BaseModel):
    """Response for the global reindex action."""

    stores_reconciled: int = Field(description="Number of casebases reconciled")
    message: str = Field(description="Human-readable status message")


class AdminFactoryResetResponse(BaseModel):
    """Response for the composite factory-reset action."""

    actions: list[str] = Field(description="Reset steps that were executed")
    message: str = Field(description="Human-readable status message")


class AdminMaintenanceState(BaseModel):
    """State of the global maintenance flag (request body and response).

    Persisted in the ``ApplicationSettings`` singleton and cached on
    ``app.state`` — see ``hivegent.server.maintenance``.
    """

    enabled: bool = Field(description="Whether maintenance mode is active")


class AdminUserInfo(BaseModel):
    """Summary information about a user known to the system.

    Only users that have left a footprint in the local database (a
    conversation, document, token, or memory) appear here — Hivegent is
    not an identity store, so it does not enumerate every account from
    the upstream OIDC provider.
    """

    id: str = Field(description="User identifier")
    document_count: int = Field(description="Number of documents owned by the user")
    conversation_count: int = Field(description="Number of conversations owned")
    has_workspace: bool = Field(
        description="Whether a workspace directory exists on disk",
    )


class AdminListUsersResponse(BaseModel):
    """Response for the admin list-users endpoint."""

    users: list[AdminUserInfo] = Field(description="Users with local data")


class AdminGroupInfo(BaseModel):
    """Summary information about a group in the system."""

    id: str = Field(description="Group identifier")
    document_count: int = Field(description="Number of documents owned by the group")
    has_workspace: bool = Field(
        description="Whether a workspace directory exists on disk",
    )


class AdminListGroupsResponse(BaseModel):
    """Response for the admin list-groups endpoint."""

    groups: list[AdminGroupInfo] = Field(description="All groups known to the system")
