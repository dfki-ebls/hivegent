"""Shared types and server-facing schemas for Hivegent."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Self, get_args

from pydantic import BaseModel, Field, PrivateAttr, model_validator
from pydantic_ai.settings import ThinkingEffort
from pydantic_ai.ui.vercel_ai.request_types import UIMessage

from .chunkers import ChunkingSpec
from .config import ADMIN_ROLE, settings
from .converters import ConversionSpec
from .db.conversations import ConversationSummary
from .entries import stem_path_from_reference
from .prompts import Personality
from .security import (
    UnsafeUrlError,
    require_safe_url_shape,
    validate_external_headers,
)

__all__ = [
    "AdminFactoryResetResponse",
    "AdminGroupInfo",
    "AdminListGroupsResponse",
    "AdminListUsersResponse",
    "AdminMaintenanceState",
    "AdminReindexResponse",
    "AdminResetResponse",
    "AdminUserInfo",
    "BulkDeleteConversationsResponse",
    "BulkDeleteDocumentsResponse",
    "BulkDeleteUserDataResponse",
    "BulkOperationCompleteEvent",
    "BulkOperationProgressEvent",
    "ChatRequestConfig",
    "ClearMemoryResponse",
    "CollectionCompleteEvent",
    "CollectionProgressEvent",
    "CollectionUploadResponse",
    "CompactConversationRequest",
    "CompactConversationResponse",
    "ConversationListResponse",
    "CreateDirectoryRequest",
    "CreateDirectoryResponse",
    "DeleteConversationResponse",
    "DeleteDirectoryRequest",
    "DeleteDirectoryResponse",
    "DeleteDocumentResponse",
    "DirectoryEntry",
    "DirectoryTreeResponse",
    "DocumentFilter",
    "DocumentInfo",
    "GenerateTitleRequest",
    "GenerateTitleResponse",
    "GroupInfo",
    "GroupListResponse",
    "LlmConfig",
    "McpOAuth2Config",
    "McpServerConfig",
    "McpTestResponse",
    "MoveDocumentRequest",
    "MoveDocumentResponse",
    "AssetProcessingMode",
    "OperationErrorEvent",
    "OperationStageEvent",
    "PipelineSpec",
    "RechunkCompleteEvent",
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
    "resolve_llm_config",
]


@dataclass(slots=True, frozen=True)
class DocumentFilter:
    """Include and exclude filter applied to document-level operations.

    Entries use the workspace UI's conventions: ``dir/`` (trailing slash)
    selects a directory subtree including the directory itself, ``/``
    selects the whole store, and a file entry selects the logical document
    — the file plus its same-stem siblings (description and original) and
    its ``.assets`` payload, mirroring how the inventory groups files into
    entries.

    A path passes when no excluded entry selects it and, if an include
    list is set, an included entry selects it or it is an ancestor
    directory of an included entry (so listings can traverse into included
    subtrees).  ``included=None`` means no include restriction, while an
    empty set hides the whole store — the store was not part of a
    non-empty whitelist.
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
        stem = stem_path_from_reference(entry)
        return (
            stem_path_from_reference(path) == stem
            or path.startswith(f"{stem}.assets/")
        )

    def __call__(self, path: str) -> bool:
        """Return whether the file or directory *path* passes the filter."""
        if any(self._selects(entry, path) for entry in self.excluded):
            return False
        if self.included is None:
            return True
        return any(
            self._selects(entry, path) or entry.startswith(f"{path}/")
            for entry in self.included
        )


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
    """

    id: str
    email: str | None = None
    name: str | None = None
    read_groups: frozenset[str] = field(default_factory=frozenset)
    write_groups: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_groups(self) -> frozenset[str]:
        """Return all groups the user belongs to."""
        return self.read_groups | self.write_groups

    @property
    def is_admin(self) -> bool:
        """Whether the user holds the fixed admin role."""
        return ADMIN_ROLE in self.roles


class LlmConfig(BaseModel):
    """Client-provided LLM configuration overrides.

    User-provided ``base_url`` values run through the SSRF filter.
    Server-configured ``base_url`` values are trusted operator input.
    """

    model: str = ""
    api_key: str = ""
    base_url: str | None = None

    _base_url_is_trusted: bool = PrivateAttr(default=False)

    @property
    def base_url_is_trusted(self) -> bool:
        """Whether ``base_url`` came from server configuration."""
        return self._base_url_is_trusted

    @model_validator(mode="after")
    def _check_base_url(self) -> Self:
        if self.base_url:
            require_safe_url_shape(self.base_url, "LLM base_url")
        return self


def resolve_llm_config(
    llm: LlmConfig, *, default_model: str | None = None
) -> LlmConfig:
    """Apply server defaults to a client-provided LLM configuration."""
    configured_base_url = settings.llm.base_url or None
    resolved = LlmConfig(
        model=llm.model or default_model or settings.llm.model,
        api_key=llm.api_key or settings.llm.api_key,
        base_url=llm.base_url or configured_base_url,
    )
    resolved._base_url_is_trusted = llm.base_url_is_trusted or (
        not llm.base_url and configured_base_url is not None
    )
    return resolved


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

    ``url`` and ``headers`` run through the SSRF / header-injection
    filter; private hosts require ``HIVEGENT_SECURITY__ALLOW_PRIVATE_URLS=1``
    (independent of the auth toggle).
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


type ReasoningEffort = Literal["auto", "none"] | ThinkingEffort
"""Reasoning effort accepted from the API.

Combines pydantic-ai's native effort levels (``minimal``/``low``/``medium``/
``high``/``xhigh``) with the ``auto`` (use the provider default) and ``none``
(disable thinking) sentinels.
"""

REASONING_EFFORT_VALUES: frozenset[str] = frozenset(
    {"auto", "none", *get_args(ThinkingEffort)}
)
"""Valid reasoning effort strings, used to validate untrusted request input."""


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
        description="Reasoning effort level ('auto' uses the provider default, 'none' disables thinking)",
    )
    mode: Literal["plan", "execute"] = Field(
        default="execute",
        description="Agent mode: 'plan' for planning only, 'execute' for full execution",
    )
    llm: LlmConfig = Field(default_factory=LlmConfig)
    included_documents: list[str] = Field(default_factory=list)
    excluded_documents: list[str] = Field(default_factory=list)
    tools: ToolsSpec = Field(default_factory=ToolsSpec)


class ClearMemoryResponse(BaseModel):
    """Response for clearing user memory."""

    cleared: bool = Field(description="Whether memory was cleared")
    message: str = Field(description="Status message")


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

    name: str = Field(description="Filename within the assets directory")
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

    asset_name: str = Field(description="Filename of the asset to update")
    content: str = Field(description="New content for the .md description file")


class GenerateAssetDescriptionRequest(BaseModel):
    """Request to generate an asset's companion .md description with the vision model."""

    asset_name: str = Field(description="Filename of the asset to describe")
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


class DeleteDocumentResponse(BaseModel):
    """Response for document deletion."""

    filename: str = Field(description="The deleted filename")
    message: str = Field(description="Status message")


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationSummary] = Field(
        description="List of conversation summaries",
    )
    total_count: int = Field(description="Total number of conversations")


class CompactConversationRequest(BaseModel):
    """Request to compact a conversation.

    The client sends its in-memory message history rather than relying on
    the server to re-read it: the turn that triggers auto-compaction fails
    on a context-length error and is never persisted, and a freshly minted
    conversation may not be reconciled to its server ID yet. Summarizing
    the messages the client holds avoids both races.
    """

    llm: LlmConfig = Field(default_factory=LlmConfig)
    messages: list[UIMessage] = Field(default_factory=list)


class CompactConversationResponse(BaseModel):
    """Response for conversation compaction."""

    new_conversation_id: str = Field(
        description="ID of the new compacted conversation",
    )
    summary: str = Field(description="Summary of the original conversation")
    message: str = Field(description="Status message")


class DeleteConversationResponse(BaseModel):
    """Response for conversation deletion."""

    id: str = Field(description="The deleted conversation ID")
    message: str = Field(description="Status message")


class UpdateTitleRequest(BaseModel):
    """Request to update a conversation title."""

    title: str = Field(
        description="The new title for the conversation",
        max_length=200,
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
    read_groups: list[str] = Field(
        default_factory=list,
        description="Groups with read access",
    )
    write_groups: list[str] = Field(
        default_factory=list,
        description="Groups with write access",
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
            read_groups=sorted(user.read_groups),
            write_groups=sorted(user.write_groups),
            roles=sorted(user.roles),
        )


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


class CreateDirectoryResponse(BaseModel):
    """Response for directory creation."""

    path: str = Field(description="The created directory path")
    message: str = Field(description="Status message")


class MoveDocumentRequest(BaseModel):
    """Request to move a document to a new location."""

    destination: str = Field(description="Destination relative path")


class MoveDocumentResponse(BaseModel):
    """Response for document move."""

    source: str = Field(description="Original relative path")
    destination: str = Field(description="New relative path")
    message: str = Field(description="Status message")


class MoveDirectoryRequest(BaseModel):
    """Request to move/rename a directory."""

    source: str = Field(description="Current relative path of the directory")
    destination: str = Field(description="New relative path for the directory")


class MoveDirectoryResponse(BaseModel):
    """Response for directory move."""

    source: str = Field(description="Original relative path")
    destination: str = Field(description="New relative path")
    files_moved: int = Field(description="Number of files moved")
    message: str = Field(description="Status message")


class DeleteDirectoryRequest(BaseModel):
    """Request to delete a directory."""

    path: str = Field(description="Relative path of the directory to delete")


class DeleteDirectoryResponse(BaseModel):
    """Response for directory deletion."""

    path: str = Field(description="The deleted directory path")
    files_deleted: int = Field(description="Number of files deleted")
    message: str = Field(description="Status message")


class BulkDeleteConversationsResponse(BaseModel):
    """Response for bulk conversation deletion."""

    deleted_count: int = Field(description="Number of conversations deleted")
    message: str = Field(description="Status message")


class BulkDeleteDocumentsResponse(BaseModel):
    """Response for bulk document deletion."""

    message: str = Field(description="Status message")


class BulkDeleteUserDataResponse(BaseModel):
    """Response for complete user data wipe."""

    message: str = Field(description="Status message")


class CollectionUploadResponse(BaseModel):
    """Response for collection (directory or ZIP) upload."""

    total_files: int = Field(description="Total files processed")
    markdown_files: int = Field(description="Number of markdown files uploaded")
    converted_attachments: int = Field(
        description="Number of binary attachments converted",
    )
    failed_files: list[str] = Field(
        default_factory=list,
        description="Files that failed to process",
    )
    message: str = Field(description="Status message")


class CollectionProgressEvent(BaseModel):
    """SSE progress event emitted during streaming collection upload."""

    type: Literal["progress"] = "progress"
    file: str = Field(description="File currently being processed")
    current: int = Field(description="Number of files processed so far")
    total: int = Field(description="Total number of files to process")
    status: Literal["ok", "failed"] = Field(
        description="Whether this file succeeded or failed",
    )


class CollectionCompleteEvent(CollectionUploadResponse):
    """SSE completion event emitted at the end of streaming collection upload."""

    type: Literal["complete"] = "complete"


class BulkOperationProgressEvent(BaseModel):
    """SSE progress event emitted during a bulk rechunk or reconvert operation."""

    type: Literal["progress"] = "progress"
    file: str = Field(description="File currently being processed")
    current: int = Field(description="Number of files processed so far")
    total: int = Field(description="Total number of files to process")
    status: Literal["ok", "failed"] = Field(
        description="Whether this file succeeded or failed",
    )


class BulkOperationCompleteEvent(BaseModel):
    """SSE completion event emitted at the end of a bulk operation."""

    type: Literal["complete"] = "complete"
    total_files: int = Field(description="Total files processed")
    failed_files: list[str] = Field(
        default_factory=list,
        description="Files that failed to process",
    )
    message: str = Field(description="Status message")


class OperationStageEvent(BaseModel):
    """SSE stage event emitted during a single-document operation."""

    type: Literal["stage"] = "stage"
    stage: str = Field(description="Current processing stage label")
    detail: str = Field(default="", description="Optional extra detail")


class UploadCompleteEvent(UploadDocumentResponse):
    """SSE completion event for upload and reconvert operations."""

    type: Literal["complete"] = "complete"


class RechunkCompleteEvent(BaseModel):
    """SSE completion event for rechunk operations."""

    type: Literal["complete"] = "complete"
    pipeline: str = Field(description="Chunking pipeline used")
    chunk_count: int = Field(description="Number of chunks created")


class OperationErrorEvent(BaseModel):
    """SSE error event emitted when a single-document operation fails."""

    type: Literal["error"] = "error"
    detail: str = Field(description="Error description")


class GroupInfo(BaseModel):
    """Summary information about a casebase group."""

    slug: str = Field(description="The group identifier")
    document_count: int = Field(description="Number of documents in the group")


class GroupListResponse(BaseModel):
    """Response for listing casebase groups."""

    groups: list[GroupInfo] = Field(description="List of group summaries")


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
