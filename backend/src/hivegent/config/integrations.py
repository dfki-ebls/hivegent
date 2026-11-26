"""Settings of the plugins and of the external services the app connects to."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from exar.tools.sql import Statement
from exar.typing import Effect
from pydantic import (
    AfterValidator,
    Discriminator,
    Field,
    HttpUrl,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    Tag,
    field_validator,
    model_validator,
)
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

from ..security import origin
from .common import MCP_PATH, HttpOperatorUrl, Section
from .ids import sanitize_group_id

__all__ = [
    "ApiSettings",
    "DataSourceSettings",
    "HeaderSettings",
    "HttpMcpServer",
    "McpClientCredentials",
    "McpServer",
    "McpServerSettings",
    "McpSettings",
    "PluginSettings",
    "RagoldSettings",
    "SourceSettings",
    "SqlDatabaseSettings",
    "StdioMcpServer",
]


type Groups = list[Annotated[str, AfterValidator(sanitize_group_id)]]
"""The groups a resource is narrowed to, each a sanitized id."""


def _check_disjoint(
    noun: str,
    plain: Mapping[str, str],
    secret: Mapping[str, SecretStr],
    fold: Callable[[str], str] = str,
) -> None:
    """Refuse a name, as *fold* compares it, that both *plain* and *secret* set."""
    if clash := {fold(name) for name in plain} & {fold(name) for name in secret}:
        raise ValueError(
            f"{noun} set both plain and secret: {', '.join(sorted(clash))}"
        )


def _revealed(
    plain: Mapping[str, str], secret: Mapping[str, SecretStr]
) -> dict[str, str]:
    """Every entry of *plain* and *secret*, the secrets revealed."""
    return {
        **plain,
        **{name: value.get_secret_value() for name, value in secret.items()},
    }


class PluginSettings(Section):
    """The installed plugins the operator turns on, and their options.

    A plugin is a Python package advertising an entry point in the
    ``hivegent.plugins`` group, and only the ones ``enabled`` names load.
    ``config`` maps a plugin's name to its options, validated at boot by the
    model the plugin reads them with, and a secret among them is a credential
    file like any other, ``hivegent.PLUGINS__CONFIG__<NAME>__<FIELD>``.
    ``config`` never shows in a repr or a dump.

    Configurable via ``HIVEGENT_PLUGINS__*`` environment variables.
    """

    enabled: list[str] = []
    config: dict[str, dict[str, Any]] = Field(default={}, repr=False, exclude=True)


class RagoldSettings(Section):
    """Hand-off of conversation turns to RAGold as gold benchmark entries.

    ``url`` is where the RAGold app is served, and the transfer is hidden
    while it is unset, and ``project`` names the RAGold project every entry
    is filed under.

    Configurable via ``HIVEGENT_RAGOLD__*`` environment variables.
    """

    url: HttpUrl | None = None
    project: str = Field(default="Hivegent", min_length=1)


class McpSettings(Section):
    """MCP server settings for OIDC authentication.

    Setting ``base_url``, the public URL of the ``/mcp`` endpoint, mounts it.
    ``mode`` selects ``proxy`` (FastMCP proxies the OAuth flow with a
    pre-registered ``client_id``/``client_secret``, whose id the upstream
    tokens must name as audience) or ``remote`` (clients register themselves
    with the upstream via RFC 7591 dynamic client registration, e.g. Rauthy,
    and FastMCP only verifies JWTs, which must name ``base_url``, the RFC 8707
    resource, or ``client_id`` when set, as audience).
    ``output_max_chars`` is the most text one tool return sends a client,
    whose middle is left out past it, or ``None`` to send it whole, since the
    client cannot read the rest from this server's ``/tmp``.
    ``confirmation_key`` signs the approvals a client answers for a write, so
    an answer holds across restarts.  It is a
    secret, the credential ``hivegent.MCP__CONFIRMATION_KEY``, of at least
    32 characters, and required to serve ``/mcp``.
    """

    mode: Literal["proxy", "remote"] = "proxy"
    client_id: Annotated[str, Field(min_length=1)] | None = None
    client_secret: SecretStr | None = None
    confirmation_key: Annotated[SecretStr, Field(min_length=32)] | None = None
    base_url: HttpUrl | None = None
    output_max_chars: int | None = 60_000

    @property
    def enabled(self) -> bool:
        """Whether the ``/mcp`` endpoint is served, which a ``base_url`` turns on."""
        return self.base_url is not None

    @field_validator("base_url")
    @classmethod
    def _names_the_endpoint(cls, url: HttpUrl | None) -> HttpUrl | None:
        """Refuse a URL other than the endpoint's, which is the resource its tokens name."""
        if url is not None and (url.path != MCP_PATH or url.query or url.fragment):
            raise ValueError(
                f"must be the public URL of the endpoint, such as https://hivegent.example.com{MCP_PATH}"
            )

        return url


class McpClientCredentials(Section):
    """An OAuth client the operator registered for an external MCP server.

    ``client_secret`` is a secret, so it comes from the credential
    ``hivegent.MCP_SERVERS__<ID>__OAUTH__CLIENT_SECRET`` (or the matching
    variable) rather than the TOML file.  ``issuer`` pins the authorization
    server the credentials are sent to, which the MCP server would otherwise
    choose.
    """

    client_id: str
    client_secret: SecretStr
    issuer: HttpOperatorUrl
    scopes: list[str] = []


class HeaderSettings(Section):
    """The static headers a client sends with every request to an operator endpoint.

    ``headers`` are plain values from the configuration, such as
    ``OCS-APIRequest``, and ``secret_headers`` come from credential files such
    as ``hivegent.APIS__<ID>__SECRET_HEADERS__AUTHORIZATION``.  A name may
    appear in only one of them, compared case-insensitively.
    """

    headers: dict[str, str] = {}
    secret_headers: dict[str, SecretStr] = {}

    @model_validator(mode="after")
    def _check_headers(self) -> Self:
        _check_disjoint("Headers", self.headers, self.secret_headers, str.lower)

        return self

    @property
    def request_headers(self) -> dict[str, str]:
        """Every static header with its value revealed."""
        return _revealed(self.headers, self.secret_headers)


class McpServerSettings(Section):
    """What every external MCP server shares, whatever its transport.

    ``groups`` narrows it to the members of those groups, everyone seeing it
    when empty.  ``allowed_tools`` (all when unset) and ``blocked_tools``
    select the tools offered by their names on the server.  ``effects``
    declares what tools may do by these names, else the hints a ``trusted``
    server announces decide it, while every other tool of an untrusted one is
    treated as ``execute``, and its instructions never reach the model.
    ``timeout_seconds`` bounds connecting and every
    request, and the tool list is asked for again every ``refresh_seconds``.
    """

    groups: Groups = []
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] = []
    effects: dict[str, Effect] = {}
    trusted: bool = False
    timeout_seconds: PositiveFloat = 30.0
    refresh_seconds: PositiveFloat = 300.0

    def offers(self, name: str) -> bool:
        """Whether the server's tool *name* is offered.

        >>> HttpMcpServer(url="https://x.test/mcp", blocked_tools=["drop"]).offers("drop")
        False
        """
        return (
            self.allowed_tools is None or name in self.allowed_tools
        ) and name not in self.blocked_tools


class HttpMcpServer(HeaderSettings, McpServerSettings):
    """An external MCP server reached over Streamable HTTP.

    ``type`` is ``http`` or ``streamable-http``, and ``url`` and ``headers``
    follow the notation of MCP client configs.

    ``url`` is the endpoint, reached directly like every other operator
    endpoint.  The headers (see `HeaderSettings`) and ``oauth`` authenticate
    the deployment to it, their secrets coming from credential files such as
    ``hivegent.MCP_SERVERS__<ID>__SECRET_HEADERS__AUTHORIZATION``.
    ``forward_user`` names the caller of every tool call in the header
    ``X-Hivegent-User``, the token subject of a person and the client id of
    the service principal.
    """

    type: Literal["http", "streamable-http"] | None = None
    url: HttpOperatorUrl
    oauth: McpClientCredentials | None = None
    forward_user: bool = False

    @property
    def host(self) -> str:
        """The host of the endpoint."""
        return origin(self.url)[1]


class StdioMcpServer(McpServerSettings):
    """An external MCP server run as a subprocess speaking over stdio.

    ``type`` is ``stdio``, and ``command``, ``args``, ``env`` and ``cwd``
    follow the notation of MCP client configs.

    ``command`` and ``args`` start it, in ``cwd`` when set, with the ``env``
    values and the ``secret_env`` credentials, such as
    ``hivegent.MCP_SERVERS__<ID>__SECRET_ENV__API_KEY``, on top of the
    minimal base environment of the MCP client (``PATH``, ``HOME``, and a few
    more), never the whole environment of the service.  It runs with the
    service's own privileges, outside the egress confinement of the HTTP
    clients.  A name may appear in only one of ``env`` and ``secret_env``.
    """

    type: Literal["stdio"] | None = None
    command: str = Field(min_length=1)
    args: list[str] = []
    env: dict[str, str] = {}
    secret_env: dict[str, SecretStr] = {}
    cwd: Path | None = None

    @property
    def host(self) -> str:
        """What the server runs as, which has no host."""
        return self.command

    @model_validator(mode="after")
    def _check_env(self) -> Self:
        _check_disjoint("Variables", self.env, self.secret_env)

        return self

    @property
    def process_env(self) -> dict[str, str]:
        """Every configured variable with its value revealed."""
        return _revealed(self.env, self.secret_env)


def _transport(server: object) -> str | None:
    """The transport tag of a server entry, the way MCP client configs state it.

    The standard ``type`` names it, and without one a ``command`` means stdio
    and anything else HTTP.  A ``type`` this does not know, such as ``sse``,
    is returned as it is and fails validation by name.
    """
    if isinstance(server, StdioMcpServer):
        return "stdio"

    if isinstance(server, HttpMcpServer):
        return "http"

    if not isinstance(server, dict):
        return None

    match server.get("type"):
        case None:
            return "stdio" if "command" in server else "http"
        case "streamable-http":
            return "http"
        case other:
            return str(other)


type McpServer = Annotated[
    Annotated[HttpMcpServer, Tag("http")] | Annotated[StdioMcpServer, Tag("stdio")],
    Discriminator(_transport),
]
"""An external MCP server in the notation of MCP client configs (``.mcp.json``, Claude Desktop)."""


class DataSourceSettings(Section):
    """What a database and an API share.

    ``groups`` narrows it to their members, everyone and a service principal
    seeing it when empty.  ``instructions`` tell the model what the data means
    or when to use it, and ``timeout_seconds`` bounds every query or request.
    """

    groups: Groups = []
    instructions: str = ""
    timeout_seconds: PositiveFloat = 20.0


class SqlDatabaseSettings(DataSourceSettings):
    """A SQL database the agent reads through typed selections and the operator's statements.

    ``url`` is a SQLAlchemy URL without its password, which is the secret
    ``hivegent.DATABASES__<ID>__PASSWORD``.  The model never writes SQL: it
    lists, describes, and selects from the ``tables`` alone, a selection
    compiled with every value bound, and calls each of the ``statements`` as a
    tool of its own whose arguments are its typed bound parameters.  A
    selection and a statement of effect ``read`` run in a read-only
    transaction, one of effect ``write`` commits and asks the user first, all
    with a statement timeout of ``timeout_seconds`` and at most ``max_rows``
    rows returned.  The privileges of the account still bound what a statement
    touches, so grant it what the tables and statements need alone.
    """

    url: str
    password: SecretStr | None = None
    tables: list[str] = []
    statements: dict[str, Statement] = {}
    max_rows: PositiveInt = 100

    @model_validator(mode="after")
    def _check_offering(self) -> Self:
        if not self.tables and not self.statements:
            raise ValueError("A database needs tables or statements")

        return self

    @property
    def host(self) -> str:
        """The host of the URL, empty for a database without one."""
        return make_url(self.url).host or ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, url: str) -> str:
        try:
            password = make_url(url).password
        except ArgumentError as exc:
            raise ValueError(str(exc)) from exc

        if password is not None:
            raise ValueError("The URL names no password, which is a credential")

        return url


class ApiSettings(HeaderSettings, DataSourceSettings):
    """An HTTP API whose operations the agent calls, one tool each.

    ``spec`` is the path of its OpenAPI 3 document, JSON or YAML, and only the
    ``operations`` named by their ``operationId`` are offered.  A ``GET`` or
    ``HEAD`` reads and any other method asks the user first, and every request
    goes to the origin of ``url`` with the headers (see `HeaderSettings`),
    whose secrets come from credential files such as
    ``hivegent.APIS__<ID>__SECRET_HEADERS__AUTHORIZATION``.
    A response beyond ``max_response_bytes`` is cut.
    """

    url: HttpOperatorUrl
    spec: Path
    operations: list[str] = Field(min_length=1)
    max_response_bytes: PositiveInt = 1_000_000

    @property
    def host(self) -> str:
        """The host of the URL."""
        return origin(self.url)[1]


class SourceSettings(Section):
    """An external source the server mirrors and indexes, read-only by design.

    ``connector`` names how it is reached, ``webdav`` and ``local`` built in and every
    other contributed by an enabled plugin, ``url`` its root, which a
    connector reached over HTTP needs and ``local`` has none of, and
    ``options`` the connector's own settings, validated by it at boot.  Every
    request goes to the host of ``url`` or one of ``hosts`` and nowhere
    else, authenticated by ``username`` and ``password``, which is a secret
    and so comes from the credential ``hivegent.SOURCES__<ID>__PASSWORD``.
    ``groups`` narrows it to their members, everyone seeing it when empty.
    It is synced every ``interval_minutes``.  A file the source reports
    deleted or one missing from a complete listing leaves the mirror at
    once, and ``max_sweep_share`` brakes a mass deletion of the latter, as
    :func:`hivegent.sources.sync.plan_sync` says.
    Its files are converted by the ``conversion`` and chunked by the
    ``chunking`` pipeline, each a registered id checked at boot, ``auto`` by
    default as for an upload.
    """

    connector: str
    url: HttpUrl | None = None
    hosts: list[str] = []
    username: str | None = None
    password: SecretStr | None = None
    options: dict[str, Any] = Field(default={}, repr=False)
    groups: Groups = []
    interval_minutes: PositiveFloat = 15.0
    max_sweep_share: float = Field(default=0.5, ge=0.0, le=1.0)
    conversion: str = "auto"
    chunking: str = "auto"

    @model_validator(mode="after")
    def _check_credentials(self) -> Self:
        if self.password is not None and self.username is None:
            raise ValueError("A source's password needs a username")

        if self.url is None and (self.hosts or self.username is not None):
            raise ValueError("A source without a url takes no hosts or credentials")

        return self
