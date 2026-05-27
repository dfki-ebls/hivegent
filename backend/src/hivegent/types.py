"""Shared types and server-facing schemas for Hivegent."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from .chunkers import ChunkingSpec
from .config import settings
from .converters import ConversionSpec
from .db.conversations import ConversationSummary
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
    "AdminReindexResponse",
    "AdminResetResponse",
    "AdminUserInfo",
    "BulkDeleteConversationsResponse",
    "BulkDeleteDocumentsResponse",
    "BulkDeleteUserDataResponse",
    "BulkOperationCompleteEvent",
    "BulkOperationProgressEvent",
    "BulkRevokeTokensResponse",
    "ChatRequestConfig",
    "ClearMemoryResponse",
    "CollectionCompleteEvent",
    "CollectionProgressEvent",
    "CollectionUploadResponse",
    "CompactConversationRequest",
    "CompactConversationResponse",
    "ConversationListResponse",
    "CreateConversationResponse",
    "CreateDirectoryRequest",
    "CreateDirectoryResponse",
    "CreateTokenRequest",
    "CreateTokenResponse",
    "DeleteConversationResponse",
    "DeleteDirectoryRequest",
    "DeleteDirectoryResponse",
    "DeleteDocumentResponse",
    "DirectoryEntry",
    "DirectoryTreeResponse",
    "DocumentFilter",
    "DocumentInfo",
    "DocumentListResponse",
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
    "TokenInfo",
    "ToolInfo",
    "ToolsSpec",
    "UpdateTitleRequest",
    "UploadCompleteEvent",
    "UploadDocumentResponse",
    "User",
    "UserResponse",
    "resolve_llm_config",
]


@dataclass(slots=True, frozen=True)
class DocumentFilter:
    """Include and exclude filter applied to document-level operations."""

    included: frozenset[str] = field(default_factory=frozenset)
    excluded: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def _matches(entry: str, filepath: str) -> bool:
        """Check whether a filter entry matches a filepath."""
        if entry.endswith("/"):
            return filepath.startswith(entry)
        return filepath == entry

    def __call__(self, filepath: str) -> bool:
        """Return whether the filepath passes the filter."""
        if self.included and not any(
            self._matches(entry, filepath) for entry in self.included
        ):
            return False
        return not (
            self.excluded
            and any(self._matches(entry, filepath) for entry in self.excluded)
        )


@dataclass(slots=True, frozen=True)
class User:
    """Authenticated user information with group membership.

    Admin status is derived on every access from
    :attr:`~hivegent.config.GroupSettings.admin_group` membership — no
    duplicate flag is stored anywhere.  Grant admin by adding a user to
    that group in the IdP (or in the local workspace for dev).
    """

    id: str
    email: str | None = None
    name: str | None = None
    read_groups: frozenset[str] = field(default_factory=frozenset)
    write_groups: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_groups(self) -> frozenset[str]:
        """Return all groups the user belongs to."""
        return self.read_groups | self.write_groups

    @property
    def is_admin(self) -> bool:
        """Whether the user is a member of the configured admin group."""
        admin_group = settings.groups.admin_group
        return bool(admin_group) and admin_group in self.all_groups


class LlmConfig(BaseModel):
    """Client-provided LLM configuration overrides.

    ``base_url`` runs through the SSRF filter (see
    :mod:`hivegent.security`). Operators that point at a self-hosted LLM
    on a private network must set ``HIVEGENT_SECURITY__ALLOW_PRIVATE_URLS=1``;
    this is independent of ``HIVEGENT_AUTH__ENABLE``.
    """

    model: str = ""
    api_key: str = ""
    base_url: str | None = None

    @model_validator(mode="after")
    def _check_base_url(self) -> Self:
        if self.base_url:
            require_safe_url_shape(self.base_url, "LLM base_url")
        return self


def resolve_llm_config(
    llm: LlmConfig, *, default_model: str | None = None
) -> LlmConfig:
    """Apply server defaults to a client-provided LLM configuration."""
    return LlmConfig(
        model=llm.model or default_model or settings.llm.model,
        api_key=llm.api_key or settings.llm.api_key,
        base_url=llm.base_url or settings.llm.base_url or None,
    )


class AssetProcessingMode(str, Enum):
    """How extracted assets (images, etc.) are handled during ingestion.

    ``off`` drops the assets entirely and strips their markdown
    references; ``store`` persists the raw bytes alongside the document
    without describing them; ``describe`` runs the asset-triage pipeline
    and describes the assets it deems semantically meaningful.
    """

    OFF = "off"
    STORE = "store"
    DESCRIBE = "describe"


class PipelineSpec(BaseModel):
    """Bundled conversion and chunking pipeline selection."""

    conversion: ConversionSpec = Field(default_factory=ConversionSpec)
    chunking: ChunkingSpec = Field(default_factory=ChunkingSpec)
    process_assets: AssetProcessingMode = AssetProcessingMode.DESCRIBE


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


class TokenInfo(BaseModel):
    """Information about a personal access token without the token value."""

    id: str = Field(description="The token ID")
    name: str = Field(description="The token name")
    created_at: datetime = Field(description="When the token was created")
    expires_at: datetime | None = Field(
        default=None,
        description="When the token expires",
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="When the token was last used",
    )


class ChatRequestConfig(BaseModel):
    """Configuration for chat requests, passed via request body."""

    conversation_id: str = Field(default="", description="The conversation ID")
    personality: Personality = Field(default=Personality.DEFAULT)
    system_message: str = Field(
        default="",
        description="Custom system message (used when personality is 'custom')",
    )
    reasoning_effort: Literal["auto", "none", "low", "medium", "high"] = Field(
        default="auto",
        description="Reasoning effort level ('auto' means unset/provider default)",
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


class CreateConversationResponse(BaseModel):
    """Response for conversation creation."""

    id: str = Field(description="The unique conversation ID")


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


class DocumentListResponse(BaseModel):
    """Response for listing documents."""

    documents: list[DocumentInfo] = Field(description="List of document metadata")
    total_count: int = Field(description="Total number of documents")


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
    """Request to compact a conversation."""

    llm: LlmConfig = Field(default_factory=LlmConfig)


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
    it the same way the server does, by checking ``admin_group`` against
    ``read_groups | write_groups``.  See :class:`SettingsResponse`.
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

    @staticmethod
    def from_user(user: User) -> "UserResponse":
        """Create a serializable response from an authenticated user."""

        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            read_groups=sorted(user.read_groups),
            write_groups=sorted(user.write_groups),
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
    has_api_key: bool = Field(description="Whether a server-side API key is configured")
    base_url: str = Field(description="Default base URL for the LLM provider")
    user: UserResponse = Field(description="Authenticated user information")
    admin_group: str = Field(
        description=(
            "Group name whose members are administrators.  Empty string "
            "disables the admin gate.  Lets the client derive admin "
            "status the same way the server does."
        ),
    )


class GenerateTitleRequest(BaseModel):
    """Request to generate a title using an LLM."""

    llm: LlmConfig = Field(default_factory=LlmConfig)


class GenerateTitleResponse(BaseModel):
    """Response from title generation."""

    title: str = Field(description="The generated title")


class CreateTokenRequest(BaseModel):
    """Request to create a personal access token."""

    name: str = Field(description="A name for the token")
    expires_in_days: int | None = Field(
        default=None,
        description="Optional expiration in days",
    )


class CreateTokenResponse(BaseModel):
    """Response from token creation."""

    token: str = Field(description="The raw token (only shown once)")
    id: str = Field(description="The token ID for management")
    name: str = Field(description="The token name")
    created_at: datetime = Field(description="When the token was created")
    expires_at: datetime | None = Field(
        default=None,
        description="When the token expires",
    )


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


class BulkRevokeTokensResponse(BaseModel):
    """Response for bulk token revocation."""

    revoked_count: int = Field(description="Number of tokens revoked")
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


class AdminUserInfo(BaseModel):
    """Summary information about a user known to the system.

    Only users that have left a footprint in the local database (a
    conversation, document, token, or memory) appear here — Hivegent is
    not an identity store, so it does not enumerate every account from
    the upstream OIDC provider.
    """

    id: str = Field(description="User identifier")
    email: str | None = Field(default=None, description="User email address")
    name: str | None = Field(default=None, description="User display name")
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
    member_count: int = Field(description="Number of members in the group")
    has_workspace: bool = Field(
        description="Whether a workspace directory exists on disk",
    )


class AdminListGroupsResponse(BaseModel):
    """Response for the admin list-groups endpoint."""

    groups: list[AdminGroupInfo] = Field(description="All groups known to the system")
