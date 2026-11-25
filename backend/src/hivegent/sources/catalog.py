"""The configured sources, each with its connector and its confined client.

Built once at import, so a source naming an unknown connector or pipeline,
or options its connector refuses, stops the boot.  A client opens when its
connector first asks for it and closes with :data:`CLIENTS`.
"""

from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

import httpx2
from pydantic import BaseModel
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT

from ..config import reveal, settings
from ..config.integrations import SourceSettings
from ..pipelines import (
    AUTO,
    CHUNKERS,
    CONVERTERS,
)
from ..plugins.api import ConnectorFactory
from ..plugins.loader import merge, parse_options
from ..security import UrlPolicy, create_confined_client
from ..shared.connectors import Connector
from ..store import SourceScope, Workspace
from .local import local
from .webdav import webdav

__all__ = ["CLIENTS", "SOURCES", "MirroredSource", "chunking_of", "conversion_of"]

CLIENTS = AsyncExitStack()
"""Closes the clients of every source once the app, which enters it, stops."""


BUILT_IN: Mapping[str, ConnectorFactory] = {"webdav": webdav, "local": local}
"""The connectors every deployment has, beside the ones enabled plugins contribute."""


@dataclass(slots=True)
class _Host:
    """The `hivegent.plugins.api.SourceHost` one source's connector builds with."""

    id: str
    source: SourceSettings = field(repr=False)
    client: httpx2.AsyncClient | None = None
    configured: bool = False

    @property
    def url(self) -> httpx2.URL:
        if self.source.url is None:
            raise ValueError(f"sources.{self.id}.url is required by its connector")

        return httpx2.URL(str(self.source.url))

    def config[C: BaseModel](self, model: type[C], /) -> C:
        self.configured = True

        return parse_options(model, self.source.options, f"sources.{self.id}.options")

    def http_client(self) -> httpx2.AsyncClient:
        """The client, opened on the first request, confined to the source's hosts and authenticated as it is configured."""
        if self.client is not None:
            return self.client

        source = self.source
        auth = (
            None
            if source.username is None
            else httpx2.BasicAuth(source.username, reveal(source.password) or "")
        )
        self.client = create_confined_client(
            policy=UrlPolicy(allow_hosts=(self.url.host, *source.hosts)),
            timeout=httpx2.Timeout(
                DEFAULT_HTTP_TIMEOUT, connect=settings.network.connect_timeout_seconds
            ),
            auth=auth,
        )
        CLIENTS.push_async_callback(self.client.aclose)

        return self.client


@dataclass(slots=True, eq=False)
class MirroredSource:
    """One configured source, its connector, and its pipelines in its settings."""

    id: str
    config: SourceSettings
    connector: Connector

    @classmethod
    def build(
        cls,
        source_id: str,
        config: SourceSettings,
        factories: Mapping[str, ConnectorFactory],
    ) -> MirroredSource:
        """The source *config* describes, its connector built but its client not yet open.

        Raises:
            ValueError: For an unknown connector, options it refuses or never
                reads, or a pipeline that is unknown or unavailable.
        """
        factory = factories.get(config.connector)

        if factory is None:
            raise ValueError(
                f"sources.{source_id}.connector names no connector: {config.connector!r}"
            )

        host = _Host(source_id, config)
        connector = factory(host)

        if config.options and not host.configured:
            raise ValueError(f"sources.{source_id} takes no options")

        for stage, registry, name in (
            ("conversion", CONVERTERS, config.conversion),
            ("chunking", CHUNKERS, config.chunking),
        ):
            registration = registry.get(name)

            if name != AUTO and (registration is None or not registration.available()):
                raise ValueError(
                    f"sources.{source_id}.{stage} names no available pipeline: {name!r}"
                )

        return cls(source_id, config, connector)


_FACTORIES = merge(
    "connector", BUILT_IN.items(), lambda loaded: loaded.plugin.connectors.items()
)

SOURCES: Mapping[str, MirroredSource] = {
    source_id: MirroredSource.build(source_id, config, _FACTORIES)
    for source_id, config in settings.sources.items()
}
"""Every configured source by its id."""


def _mirrored(store: Workspace) -> SourceSettings | None:
    """The settings of the source *store* mirrors, None for any other workspace or a source no longer configured."""
    scope = store.scope
    source = SOURCES.get(scope.id) if isinstance(scope, SourceScope) else None

    return None if source is None else source.config


def conversion_of(store: Workspace) -> str:
    """The conversion pipeline of the documents of *store*, its source's in a mirror, else the operator's `auto`."""
    return AUTO if (source := _mirrored(store)) is None else source.conversion


def chunking_of(store: Workspace) -> str:
    """The chunking pipeline of the documents of *store*, its source's in a mirror, else the operator's `auto`."""
    return AUTO if (source := _mirrored(store)) is None else source.chunking
