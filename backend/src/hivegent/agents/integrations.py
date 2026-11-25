"""Every external service the operator configured, offered to runs and listed to admins alike.

An :class:`Integration` is an MCP server, database, or API that contributes tools named
``<id>_<name>``, narrowed to the members of its ``groups`` or the global scope's when it has none.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Protocol

from pydantic_ai.toolsets import AbstractToolset

from ..store import User
from ..types import Integration as IntegrationInfo
from ..types import ToolInfo
from .catalog import CATALOG
from .common import UserDeps
from .external import SOURCES

__all__ = [
    "INTEGRATIONS",
    "Integration",
    "integration_tool_infos",
    "integration_toolsets",
    "integrations_lifespan",
    "is_integration_tool",
]


class Integration(Protocol):
    """What the registry needs of an MCP server, database, or API."""

    @property
    def id(self) -> str: ...

    @property
    def groups(self) -> Sequence[str]: ...

    @property
    def toolset(self) -> AbstractToolset[UserDeps]:
        """The tools as a run offers them."""
        ...

    def tool_infos(self) -> list[ToolInfo]:
        """The tools it offers right now."""
        ...

    def integration(self) -> IntegrationInfo:
        """It as the admin listing shows it, without any secret."""
        ...

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        """Whatever it keeps running or open while the app runs."""
        ...


INTEGRATIONS: tuple[Integration, ...] = (*CATALOG, *SOURCES)
"""Every configured integration."""


def is_integration_tool(name: str) -> bool:
    """Whether *name* is prefixed by the id of an integration, as its tools are.

    Its own list is not consulted, so a name stays valid while a server is down.
    """
    integration_id, sep, _ = name.partition("_")

    return bool(sep) and any(
        integration.id == integration_id for integration in INTEGRATIONS
    )


def _visible(user: User) -> list[Integration]:
    return [
        integration for integration in INTEGRATIONS if user.sees(integration.groups)
    ]


def integration_toolsets(user: User) -> list[AbstractToolset[UserDeps]]:
    """The toolsets of the integrations *user* sees, for a run to compose."""
    return [integration.toolset for integration in _visible(user)]


def integration_tool_infos(user: User) -> list[ToolInfo]:
    """The tools of the integrations *user* sees, grouped by integration."""
    return [info for integration in _visible(user) for info in integration.tool_infos()]


@asynccontextmanager
async def integrations_lifespan() -> AsyncGenerator[None]:
    """Run every integration for as long as the app runs."""
    async with AsyncExitStack() as stack:
        for integration in INTEGRATIONS:
            await stack.enter_async_context(integration.lifespan())

        yield
