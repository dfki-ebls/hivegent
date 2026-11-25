"""API models of the admin console, external sources, and plugins."""

from datetime import datetime
from typing import Annotated, Literal

from exar.typing import Effect
from pydantic import BaseModel, Field

__all__ = [
    "AdminGroupInfo",
    "AdminMaintenanceState",
    "AdminOverviewResponse",
    "AdminReindexResponse",
    "AdminUserInfo",
    "DataIntegration",
    "Integration",
    "McpIntegration",
    "PluginFeatureInfo",
    "PluginInfo",
    "SourceStatus",
]


class AdminUserInfo(BaseModel):
    """A user who stored something, Hivegent being no identity store that lists every account."""

    id: str = Field(description="User identifier")
    document_count: int = Field(description="Entries in the user's workspace")
    conversation_count: int = Field(description="Conversations the user owns")


class AdminGroupInfo(BaseModel):
    """A group whose workspace or library was used, membership living in the token alone."""

    id: str = Field(description="Group identifier")
    document_count: int = Field(description="Entries in the group's workspace")


class AdminOverviewResponse(BaseModel):
    """Response for the admin overview endpoint."""

    users: list[AdminUserInfo] = Field(description="Users with local data")
    groups: list[AdminGroupInfo] = Field(description="Groups with local data")


class AdminReindexResponse(BaseModel):
    """Response for the global reindex action."""

    message: str = Field(description="Human-readable status message")


class McpIntegration(BaseModel):
    """A configured external MCP server as the admin listing shows it, without secrets.

    ``tools`` maps each tool the server offers, by its name there, to its
    effect, and is empty while the server is not ``connected``.
    """

    kind: Literal["mcp"] = "mcp"
    id: str
    host: str
    groups: list[str]
    trusted: bool
    connected: bool
    error: str | None
    tools: dict[str, Effect]


class DataIntegration(BaseModel):
    """A configured SQL database or OpenAPI API as the admin listing shows it, without secrets.

    ``host`` is empty for a database without one, and ``tools`` maps each
    tool, by its name as the model sees it, to its effect.
    """

    kind: Literal["database", "api"]
    id: str
    host: str
    groups: list[str]
    tools: dict[str, Effect]


type Integration = Annotated[
    McpIntegration | DataIntegration, Field(discriminator="kind")
]


class SourceStatus(BaseModel):
    """A configured external source as the admin listing shows it, without secrets.

    ``synced_at`` is when its last sync ended, ``error`` why it failed or
    which files it could not mirror, ``held_deletes`` how many deletions wait
    for an admin to confirm, and ``items`` how many files the mirror holds.
    ``requested`` is the sync an admin asked for that no sync has finished,
    and ``running`` whether a sync runs, ``done`` of ``total`` files in.
    """

    id: str
    connector: str
    host: str
    groups: list[str]
    interval_minutes: float
    requested: Literal["sync", "confirm"] | None
    running: bool
    done: int
    total: int
    synced_at: datetime | None
    error: str | None
    held_deletes: int
    items: int


class PluginFeatureInfo(BaseModel):
    """A feature a plugin contributes, each tool by name with its effect."""

    id: str
    tools: dict[str, Effect]


class PluginInfo(BaseModel):
    """An enabled plugin and every id it contributes, as the admin listing shows it."""

    name: str
    features: list[PluginFeatureInfo]
    converters: list[str]
    chunkers: list[str]
    skills: list[str]


class AdminMaintenanceState(BaseModel):
    """State of the global maintenance flag (request body and response).

    Persisted in the ``ApplicationSettings`` singleton and cached on
    ``app.state``.  See ``hivegent.server.maintenance``.
    """

    enabled: bool = Field(description="Whether maintenance mode is active")
