"""Plugins load from the entry points the operator enabled, held to the host's policy."""

from collections.abc import Mapping, Sequence
from importlib.metadata import EntryPoint

import pytest
from exar.tools import effect
from pydantic import BaseModel

from hivegent.agents import capabilities
from hivegent.plugins import api, loader
from hivegent.server.routes import admin


class LookupConfig(BaseModel):
    base_url: str


def lookup(host: api.Host) -> api.Plugin:
    config = host.config(LookupConfig)

    @effect("read")
    async def lookup_term(term: str) -> str:
        """Look up a term."""
        return f"{config.base_url}/{term}"

    return api.Plugin(features=[api.Feature(id="lookup", tools=[lookup_term])])


def broken(_host: api.Host) -> api.Plugin:
    raise RuntimeError("boom")


def copycat(_host: api.Host) -> api.Plugin:
    return api.Plugin(features=[api.Feature(id="lookup", tools=[])])


def silent(_host: api.Host) -> api.Plugin:
    async def silent_call() -> None:
        """Do something unstated."""

    return api.Plugin(features=[api.Feature(id="silent", tools=[silent_call])])


_INSTALLED = {plugin.__name__: plugin for plugin in (lookup, broken, copycat, silent)}


def _installed(*, group: str, name: str) -> list[EntryPoint]:
    """The entry points of the plugins above, so none comes from the environment."""
    return [EntryPoint(name, f"{__name__}:{name}", group)] if name in _INSTALLED else []


def _load(
    enabled: Sequence[str], options: Mapping[str, Mapping[str, object]]
) -> tuple[loader.LoadedPlugin, ...]:
    return loader.load_plugins(enabled, options, _installed)


def test_an_enabled_plugin_contributes_and_is_listed_by_effect() -> None:
    (loaded,) = _load(["lookup"], {"lookup": {"base_url": "https://x"}})
    feature = capabilities._plugin_feature(loaded.name, loaded.plugin.features[0])
    info = admin._plugin_info(loaded)

    assert feature.tool_names == {"lookup_term"}
    assert feature.modes == {"read", "write", "interactive"}
    assert info.name == "lookup"
    assert [(f.id, f.tools) for f in info.features] == [
        ("lookup", {"lookup_term": "read"})
    ]


@pytest.mark.parametrize(
    ("enabled", "options", "error"),
    [
        (["missing"], {}, "0 installed packages"),
        (["broken"], {}, "failed to load: boom"),
        (["lookup"], {"lookup": {"base_url": "x", "typo": 1}}, "failed to load"),
        (["copycat"], {"copycat": {}}, "takes no plugins.config"),
        ([], {"lookup": {}}, "not enabled"),
    ],
)
def test_the_boot_refuses_a_plugin_it_cannot_trust(
    enabled: list[str], options: dict[str, dict[str, object]], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        _load(enabled, options)


def test_a_refused_option_never_shows_its_value() -> None:
    with pytest.raises(ValueError, match="invalid plugins.config.lookup") as refused:
        _load(["lookup"], {"lookup": {"base_url": "x", "key": "s3cret"}})

    assert "s3cret" not in str(refused.value)


def test_a_plugin_tool_must_declare_its_effect() -> None:
    (loaded,) = _load(["silent"], {})

    with pytest.raises(ValueError, match="declares no Effect"):
        capabilities._plugin_feature(loaded.name, loaded.plugin.features[0])


@pytest.mark.parametrize(
    ("built_in", "error"),
    [
        (["lookup"], "'lookup' of plugin 'lookup' is taken by Hivegent"),
        ([], "'lookup' of plugin 'copycat' is taken by plugin 'lookup'"),
    ],
)
def test_no_contribution_shadows_another(built_in: list[str], error: str) -> None:
    loaded = _load(["lookup", "copycat"], {"lookup": {"base_url": "https://x"}})

    with pytest.raises(ValueError, match=error):
        _ = loader.merge(
            "feature",
            ((id, None) for id in built_in),
            lambda plugin: ((feature.id, None) for feature in plugin.plugin.features),
            loaded,
        )
