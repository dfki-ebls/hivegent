"""API models of the user, the frontend configuration, the model catalog, and transparency."""

from typing import Literal, Self

from pydantic import BaseModel, Field

from ..config import settings
from ..config.llm import REASONING_EFFORTS
from ..llm_config import ReasoningEffort, model_max_images
from ..store import (
    GlobalScope,
    GroupScope,
    ScopeKind,
    SourceScope,
    User,
    UserScope,
    Workspace,
)
from .chat import AttachmentLimits
from .common import MAX_DETECTION_CHARS

__all__ = [
    "FrontendConfigResponse",
    "ModelInfo",
    "OidcPublicConfig",
    "SettingsResponse",
    "TransparencyConfig",
    "TransparencyDetectionRequest",
    "TransparencyDetectionResponse",
    "TransparencyJwk",
    "TransparencyJwks",
    "UserResponse",
    "WorkspaceInfo",
]


class WorkspaceInfo(BaseModel):
    """One workspace a principal sees, with the grant it holds on it.

    ``scope`` is the prefix every path and request uses (``~``, ``@<id>``,
    ``/mnt/<id>``, or the global one), ``name`` a group's display label,
    falling back to its ID, a source's id, and empty for the personal and the
    global workspace, which the client names itself.
    """

    scope: str = Field(description="Path prefix, used to build paths")
    kind: ScopeKind = Field(
        description="Who owns it, or the external source it mirrors"
    )
    name: str = Field(
        description="A group's display label or a source's id, empty for the others"
    )
    writable: bool = Field(
        description="Whether the principal may write to it, never for a source"
    )

    @classmethod
    def build(cls, user: User) -> list[Self]:
        """Serialize the workspaces *user* sees, in scope order."""

        def info(store: Workspace) -> Self:
            match store.scope:
                case GroupScope(id=group_id):
                    name = user.group_labels.get(group_id, group_id)
                case SourceScope(id=source_id):
                    name = source_id
                case GlobalScope() | UserScope():
                    name = ""

            return cls(
                scope=store.scope.prefix,
                kind=store.scope.kind,
                name=name,
                writable=store.writable,
            )

        return [info(store) for store in user.workspaces()]


class UserResponse(BaseModel):
    """Serializable user information for API responses.

    Admin status is intentionally not on the wire.  The client derives
    it the same way the server does, by checking for the fixed ``admin``
    role in ``roles``.
    """

    id: str = Field(description="User identifier")
    email: str | None = Field(default=None, description="User email address")
    name: str | None = Field(default=None, description="User display name")
    workspaces: list[WorkspaceInfo] = Field(
        default_factory=list,
        description="Workspaces the user sees, in scope order, with the grant on each",
    )
    roles: list[str] = Field(
        default_factory=list,
        description="Global roles held by the user",
    )

    @staticmethod
    def from_user(user: User) -> UserResponse:
        """Create a serializable response from an authenticated user."""

        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            workspaces=WorkspaceInfo.build(user),
            roles=sorted(user.roles),
        )


class TransparencyConfig(BaseModel):
    """Public AI-generated text marking and detection configuration."""

    enabled: bool = Field(description="Whether watermark detection is available")
    contact_email: str | None = Field(
        description="Operator contact for external expert detector access"
    )
    minimum_watermark_tokens: int = Field(
        description="Minimum scored tokens for a conclusive negative detection result"
    )


class TransparencyDetectionRequest(BaseModel):
    """Free-form text submitted for watermark verification."""

    text: str = Field(
        min_length=1,
        max_length=MAX_DETECTION_CHARS,
        description="Text to verify",
    )


class TransparencyDetectionResponse(BaseModel):
    """Watermark verification result and its signed report."""

    status: Literal["detected", "not_detected", "inconclusive"]
    method: Literal["watermark"] = "watermark"
    message: str
    signed_report: str = Field(description="Compact, Ed25519-signed JWT report")


class TransparencyJwk(BaseModel):
    """Public Ed25519 key for detection reports and export provenance."""

    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    x: str
    kid: str
    use: Literal["sig"] = "sig"
    alg: Literal["Ed25519"] = "Ed25519"


class TransparencyJwks(BaseModel):
    """JSON Web Key Set for detection report and export provenance verification."""

    keys: list[TransparencyJwk]


class ModelInfo(BaseModel):
    """A chat model of the catalog as the composer offers it."""

    id: str = Field(description="Catalog id a chat request names the model by")
    label: str = Field(description="Display name")
    vision: bool = Field(description="Whether the model accepts image attachments")
    max_images: int | None = Field(
        description="Maximum images per turn, or null for no limit"
    )
    reasoning: list[ReasoningEffort] = Field(
        description="Reasoning efforts the model offers, none when it does not reason"
    )
    context_window: int | None = Field(description="Context window in tokens")

    @staticmethod
    def catalog() -> list[ModelInfo]:
        """The chat models of the catalog, in the order the operator listed them."""
        return [
            ModelInfo(
                id=model_id,
                label=model.label or model_id,
                vision=model.vision,
                max_images=model_max_images(model),
                reasoning=list(REASONING_EFFORTS)
                if model.reasoning is None
                else model.reasoning,
                context_window=model.context_window,
            )
            for model_id, model in settings.llm.models.items()
            if model.chat
        ]


class SettingsResponse(BaseModel):
    """What the server offers the user, with their own context."""

    models: list[ModelInfo] = Field(description="The chat models a request may name")
    default_model: str | None = Field(
        description="The model a request naming none runs on, null without a catalog"
    )
    transcription: bool = Field(
        description="Whether the speech input fallback can transcribe audio"
    )
    user: UserResponse = Field(description="Authenticated user information")
    attachments: AttachmentLimits = Field(
        description="Constraints the chat composer enforces on attachments"
    )
    transparency: TransparencyConfig = Field(
        description="AI-generated text marking and detection configuration"
    )
    ragold_url: str | None = Field(
        description="Where RAGold is served, or null when the transfer is hidden"
    )
    debug: bool = Field(description="Whether the debugging consoles serve the user")


class OidcPublicConfig(BaseModel):
    """Public OIDC parameters the SPA needs to start a login flow."""

    issuer_uri: str = Field(description="OIDC issuer the SPA authenticates against")
    client_id: str = Field(description="Public OIDC client id the SPA registers as")


class FrontendConfigResponse(BaseModel):
    """Runtime configuration the browser SPA fetches at startup.

    Served unauthenticated and carrying only public values, this is the
    frontend's single source of truth.  It reads its OIDC config from here
    instead of baking it in at build time.
    """

    oidc: OidcPublicConfig | None = Field(
        description="OIDC client configuration, or null when hivegent dev runs "
        "the server as the development user"
    )
    docs_url: str | None = Field(
        description="Where the handbook is served, or null when none is linked"
    )
