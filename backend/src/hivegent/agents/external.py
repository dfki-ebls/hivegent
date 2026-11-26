"""The operator's SQL databases and HTTP APIs as agent tools, each built once at import.

A ``databases`` entry is an `exar.tools.sql.Sql` and an ``apis`` entry an
`exar.tools.http.OpenApi`, every tool named ``<id>_<name>`` like an external
MCP server's, so a URL without a driver, a document that is unreadable, or an
operation it lacks stops the boot.  A tool declares its effect, which the host
policy decides on as for any tool: a read runs, and a write, an API's other
than a ``GET`` or ``HEAD`` or a database statement the operator declared one,
asks the user and is offered by the modes that write alone.

A source whose ``groups`` the user is in no member of is invisible to them, and
one with no ``groups`` is the global scope's, the only one a service principal
sees.  No tool is flagged as a person's own, as with an MCP server's.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import httpx2
import yaml
from exar.pydantic_ai import tool_effect, toolset
from exar.tools.http import OpenApi, OpenApiDocument
from exar.tools.sql import HostEngine, Sql
from exar.tools.web import HostClient
from pydantic_ai import FunctionToolset
from sqlalchemy import create_engine, make_url
from sqlalchemy.exc import ArgumentError

from ..config import reveal, settings
from ..config.integrations import ApiSettings, SqlDatabaseSettings
from ..http_client import create_trusted_http_client
from ..shared.l10n import Localized
from ..types import DataIntegration
from .common import UserDeps

__all__ = ["SOURCES", "DataSource"]


@dataclass(frozen=True, slots=True)
class DataSource:
    """One configured database or API, its tools, and how it is closed."""

    id: str
    kind: Literal["database", "api"]
    host: str
    groups: Sequence[str]
    toolset: FunctionToolset[UserDeps]
    instructions: Localized[str]
    close: Callable[[], Awaitable[None]]

    def __post_init__(self) -> None:
        self.toolset.timeout = settings.llm.tool_timeout_seconds

    def integration(self) -> DataIntegration:
        """The source as the admin listing shows it, without any secret."""
        return DataIntegration(
            kind=self.kind,
            id=self.id,
            host=self.host,
            groups=list(self.groups),
            tools={
                name: tool_effect(tool.metadata)
                for name, tool in self.toolset.tools.items()
            },
        )

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None]:
        """Close the engine or client once the app stops."""
        try:
            yield
        finally:
            await self.close()


def _instructions(
    lead: Localized[str], instructions: str, notes: str
) -> Localized[str]:
    """The *lead* in each language, the *instructions* of the tools, and the operator's *notes*."""
    return Localized(
        en=f"{lead.en} {instructions} {notes}".strip(),
        de=f"{lead.de} {instructions} {notes}".strip(),
    )


def _database(source_id: str, config: SqlDatabaseSettings) -> DataSource:
    """The database *config* describes, its engine not yet connected.

    Raises:
        ValueError: If the dialect of the URL has no usable driver.
    """
    url = make_url(config.url)

    if config.password is not None:
        url = url.set(password=reveal(config.password))

    try:
        engine = create_engine(url, pool_pre_ping=True)
    except (ImportError, ArgumentError) as exc:
        raise ValueError(
            f"databases.{source_id}.url has no usable driver: {exc}"
        ) from exc

    sql = Sql(
        engine=HostEngine(source_id, lambda: engine),
        prefix=f"{source_id}_",
        tables=tuple(config.tables),
        statements=config.statements,
        max_rows=config.max_rows,
        timeout_seconds=config.timeout_seconds,
    )

    async def close() -> None:
        await asyncio.to_thread(engine.dispose)

    return DataSource(
        source_id,
        "database",
        config.host,
        config.groups,
        toolset(sql.tools()),
        _instructions(
            Localized(en=f"Database {source_id}:", de=f"Datenbank {source_id}:"),
            sql.instructions(),
            config.instructions,
        ),
        close,
    )


def _api(source_id: str, config: ApiSettings) -> DataSource:
    """The API *config* describes, whose client reaches the origin of its URL alone.

    Raises:
        ValueError: If the document is invalid or lacks an operation.
    """
    client = create_trusted_http_client(
        config.url,
        timeout=httpx2.Timeout(
            config.timeout_seconds, connect=settings.network.connect_timeout_seconds
        ),
    )
    client.headers.update(config.request_headers)
    api = OpenApi(
        client=HostClient(source_id, lambda: client),
        base_url=config.url,
        document=OpenApiDocument.model_validate(
            yaml.safe_load(config.spec.read_text())
        ),
        operations=tuple(config.operations),
        prefix=f"{source_id}_",
        max_response_bytes=config.max_response_bytes,
        timeout_seconds=config.timeout_seconds,
    )

    return DataSource(
        source_id,
        "api",
        config.host,
        config.groups,
        toolset(api.tools()),
        _instructions(
            Localized(en=f"API {source_id}:", de=f"API {source_id}:"),
            api.instructions(),
            config.instructions,
        ),
        client.aclose,
    )


SOURCES: tuple[DataSource, ...] = (
    *(_database(source_id, config) for source_id, config in settings.databases.items()),
    *(_api(source_id, config) for source_id, config in settings.apis.items()),
)
"""Every configured database and API."""
