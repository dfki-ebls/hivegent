"""Loading the plugins the operator enabled, once per process on first use.

Nothing loads unless ``plugins.enabled`` names it, and an enabled plugin that
is missing, fails to build, or contributes an id already taken stops the boot
with a message naming it.  Plugins are trusted code an admin installed, so
they run in-process.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.metadata import entry_points
from typing import Any

import httpx2
from exar.helpers import get_name
from pydantic import BaseModel, ValidationError

from ..config import settings
from ..http_client import get_web_http_client
from .api import Plugin

__all__ = [
    "ENTRY_POINT_GROUP",
    "LoadedPlugin",
    "load_plugins",
    "merge",
    "parse_options",
    "plugins",
]

ENTRY_POINT_GROUP = "hivegent.plugins"

_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
"""A plugin name, kebab-case like the ids it contributes."""

_TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
"""What every model provider accepts as a tool name."""


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """An enabled plugin and what it contributed."""

    name: str
    plugin: Plugin


def parse_options[C: BaseModel](
    model: type[C], options: Mapping[str, Any] | None, where: str
) -> C:
    """Validate the operator's *options* at *where* by *model*, extra keys refused.

    Raises:
        ValueError: For options *model* refuses, naming only their keys, since
            they may hold secrets.
    """
    try:
        return model.model_validate(options or {}, extra="forbid")
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        reasons = ", ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in errors
        )

        raise ValueError(f"invalid {where}: {reasons}") from None


@dataclass(slots=True)
class _Host:
    """The :class:`~.api.Host` one plugin builds with, noting whether it read its options.

    The raw options may hold secrets, so neither its repr nor a refusal shows them.
    """

    name: str
    options: Mapping[str, Any] | None = field(repr=False)
    configured: bool = False

    def config[C: BaseModel](self, model: type[C], /) -> C:
        self.configured = True

        return parse_options(model, self.options, f"plugins.config.{self.name}")

    def http_client(self) -> httpx2.AsyncClient:
        return get_web_http_client()


def _check(name: str, plugin: object) -> Plugin:
    """Return *plugin* when it is a :class:`Plugin` whose tools models accept.

    Raises:
        TypeError: For anything but a :class:`Plugin`.
        ValueError: For a tool name a model provider refuses, or a skill
            directory that is none on disk.
    """
    if not isinstance(plugin, Plugin):
        raise TypeError(
            f"Plugin {name!r} returned {type(plugin).__name__}, not a Plugin"
        )

    tools = [get_name(tool) for feature in plugin.features for tool in feature.tools]

    if invalid := sorted(tool for tool in tools if not _TOOL_NAME.fullmatch(tool)):
        raise ValueError(
            f"Plugin {name!r} has invalid tool names: {', '.join(invalid)}"
        )

    if missing := [str(path) for path in plugin.skills if not path.is_dir()]:
        raise ValueError(
            f"Plugin {name!r} names skill directories that are none on disk: "
            f"{', '.join(missing)}"
        )

    return plugin


def load_plugins(
    enabled: Sequence[str], options: Mapping[str, Mapping[str, Any]]
) -> tuple[LoadedPlugin, ...]:
    """Build every plugin in *enabled* with its *options*, in order.

    Raises:
        ValueError: For a malformed, missing, or ambiguous plugin, one that fails
            to build or contributes something invalid, options it never reads,
            and options to a plugin that is not enabled.
    """
    if stray := sorted(set(options) - set(enabled)):
        raise ValueError(
            f"plugins.config configures plugins that are not enabled: {stray}"
        )

    loaded: list[LoadedPlugin] = []

    for name in enabled:
        if not _NAME.fullmatch(name):
            raise ValueError(f"Plugin name {name!r} is not kebab-case")

        found = entry_points(group=ENTRY_POINT_GROUP, name=name)
        if len(found) != 1:
            raise ValueError(
                f"Plugin {name!r} is enabled but {len(found)} installed packages "
                f"provide it in the {ENTRY_POINT_GROUP!r} entry point group"
            )

        host = _Host(name, options.get(name))

        try:
            plugin = next(iter(found)).load()(host)
        except Exception as exc:
            raise ValueError(f"Plugin {name!r} failed to load: {exc}") from exc

        if name in options and not host.configured:
            raise ValueError(f"Plugin {name!r} takes no plugins.config")

        loaded.append(LoadedPlugin(name, _check(name, plugin)))

    return tuple(loaded)


@cache
def plugins() -> tuple[LoadedPlugin, ...]:
    """The enabled plugins, in the order the operator listed them, built on first use."""
    return load_plugins(settings.plugins.enabled, settings.plugins.config)


def merge[T](
    kind: str,
    built_in: Iterable[tuple[str, T]],
    contributed: Callable[[LoadedPlugin], Iterable[tuple[str, T]]],
) -> dict[str, T]:
    """Merge the *built_in* contributions of *kind* with every plugin's, by id.

    No contribution shadows another, so the registries never depend on load order.

    Raises:
        ValueError: For an id taken twice, naming both owners.
    """
    merged: dict[str, T] = {}
    owners: dict[str, str] = {}
    sources = [
        ("Hivegent", built_in),
        *((f"plugin {loaded.name!r}", contributed(loaded)) for loaded in plugins()),
    ]

    for owner, entries in sources:
        for id, value in entries:
            if (taken := owners.get(id)) is not None:
                raise ValueError(f"The {kind} {id!r} of {owner} is taken by {taken}")

            owners[id] = owner
            merged[id] = value

    return merged
