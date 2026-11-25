"""The stable surface a plugin imports, and nothing else of Hivegent.

A plugin is a Python package advertising an entry point in the
``hivegent.plugins`` group, named for the plugin, whose object is a
:data:`PluginFactory`.  The server calls it once at boot with a :class:`Host`
and registers what the returned :class:`Plugin` contributes, refusing an id
that Hivegent or another plugin already takes.

Everything here is Hivegent's own, from the self-contained ``tools`` package
and ``shared``, so a plugin depends on no app internals.
A plugin imports exar and cbrkit directly, at the versions Hivegent ships.
A tool is a plain typed async function that declares its effect with
``exar.tools.effect`` and raises ``exar.tools.Retry`` for input the model can fix.
A converter or chunker is a ``Pipeline``, an exar parser or chunker built by
``exar.ingest.offloaded`` or any async callable of the same signature.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx2
from pydantic import BaseModel

from ..shared.connectors import Connector, Delta, RemoteItem
from ..shared.l10n import Localized
from ..shared.pipeline_registry import ChunkerPipeline, ConverterPipeline, Pipeline
from ..tools.base import ToolFunc

__all__ = [
    "ChunkerPipeline",
    "Connector",
    "ConnectorFactory",
    "ConverterPipeline",
    "Delta",
    "Feature",
    "Host",
    "Localized",
    "Pipeline",
    "Plugin",
    "PluginFactory",
    "RemoteItem",
    "SourceHost",
    "ToolFunc",
]


class SourceHost(Protocol):
    """What the server lends a connector for one source."""

    @property
    def url(self) -> httpx2.URL:
        """The root of the source, the one place it lies.

        Raises:
            ValueError: If the source configures no URL, which stops the boot.
        """
        ...

    def config[C: BaseModel](self, model: type[C], /) -> C:
        """The source's `options` validated by *model*, extra keys refused.

        Raises:
            ValueError: For options *model* refuses, which stops the boot.
        """
        ...

    def http_client(self) -> httpx2.AsyncClient:
        """The source's client, authenticated and confined to its declared hosts."""
        ...


type ConnectorFactory = Callable[[SourceHost], Connector]
"""What builds the `Connector` of one source, which only reads it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Feature:
    """Tools the agent is offered together, with the guidance that explains them.

    Every tool declares its effect with ``exar.tools.effect``, from which the
    server decides the modes that offer the feature and which calls wait for
    the user's approval.
    """

    id: str
    tools: Sequence[ToolFunc]
    instructions: Localized[str] | None = None
    """Kept in the cached prefix while the feature is offered."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Plugin:
    """What a plugin contributes, each id unique among Hivegent's and every plugin's."""

    features: Sequence[Feature] = ()
    converters: Mapping[str, ConverterPipeline] = field(default_factory=dict)
    chunkers: Mapping[str, ChunkerPipeline] = field(default_factory=dict)
    connectors: Mapping[str, ConnectorFactory] = field(default_factory=dict)
    """The ways to reach an external source that a source's ``connector`` may name."""
    skills: Sequence[Path] = ()
    """Read-only directories of global skills on disk, each folder in them holding a ``SKILL.md``."""


class Host(Protocol):
    """What the server lends a plugin while it builds."""

    @property
    def name(self) -> str:
        """The plugin's name, its entry point's."""
        ...

    def config[C: BaseModel](self, model: type[C], /) -> C:
        """The operator's ``plugins.config.<name>`` validated by *model*, extra keys refused.

        Raises:
            ValidationError: For options *model* refuses, which stops the boot.
        """
        ...

    def http_client(self) -> httpx2.AsyncClient:
        """The pooled web client, call it per request.

        It reaches only the public hosts ``security.web_urls`` allows, through
        the egress proxy, as ``web_fetch`` does.  Opened with the server, so a
        tool asks for it when it runs, never at build.
        """
        ...


type PluginFactory = Callable[[Host], Plugin]
"""The object a plugin's entry point names."""
