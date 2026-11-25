"""Shared building blocks of the settings sections."""

from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    HttpUrl,
    SecretStr,
    StringConstraints,
)

from ..security import OperatorUrl, UnsafeUrlError, origin

__all__ = [
    "ADMIN_ROLE",
    "API_PREFIX",
    "API_VERSION",
    "MCP_PATH",
    "CatalogId",
    "HttpOperatorUrl",
    "HttpsOperatorUrl",
    "ProviderEndpoint",
    "Section",
    "reveal",
    "unknown_reference",
]

#: The fixed role name that grants administrator privileges.  Not
#: configurable: admin is a global capability, fully independent of the
#: groups used to share knowledge.
ADMIN_ROLE = "admin"

API_VERSION = "v1"
"""The version of the REST API, which a breaking change bumps."""

API_PREFIX = f"/api/{API_VERSION}"
"""Where the REST API is mounted."""

MCP_PATH = "/mcp"
"""Where the MCP endpoint is served, the path of ``mcp.base_url``."""


def reveal(secret: SecretStr | None) -> str | None:
    """Return the plain value of an optional secret setting.

    >>> reveal(SecretStr("token")), reveal(None)
    ('token', None)
    """
    return secret.get_secret_value() if secret else None


class Section(BaseModel):
    """A settings section, refusing every key it does not declare.

    A removed or misspelled setting thus stops the boot naming it, rather than
    vanishing and leaving the default in its place.
    """

    model_config = ConfigDict(extra="forbid")


def _operator_url(url: str) -> OperatorUrl:
    try:
        _ = origin(url)
    except UnsafeUrlError as exc:
        raise ValueError(str(exc)) from exc

    return OperatorUrl(url)


type HttpOperatorUrl = Annotated[OperatorUrl, AfterValidator(_operator_url)]
"""An http or https endpoint the operator configured, which a trusted client reaches."""


def _secure_url(url: OperatorUrl) -> OperatorUrl:
    parsed = HttpUrl(url)
    host = parsed.host or ""
    loopback = host in {"127.0.0.1", "[::1]"} or host.rsplit(".", 1)[-1] == "localhost"

    if parsed.scheme != "https" and not loopback:
        raise ValueError("An http URL is only allowed for a loopback host")

    return url


type HttpsOperatorUrl = Annotated[HttpOperatorUrl, AfterValidator(_secure_url)]
"""An operator endpoint served over https, or over http on a loopback host."""


type CatalogId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
"""A provider, tool, or source id, which credential files, tool names, and paths embed."""


class ProviderEndpoint(Section):
    """A section served by the ``llm.providers`` entry ``endpoint`` names, with its key.

    One credential thus serves every use of an endpoint, and the boot refuses
    a section naming an unknown one.
    """

    endpoint: CatalogId


def unknown_reference(kind: str, name: str, target: str) -> ValueError:
    return ValueError(f"{kind} {name!r} names unknown {target}")
