# Plugins

A plugin is a Python package that adds tools, document converters, chunkers, connectors to external sources, and skill directories to Hivegent.
Administrators install and enable plugins, and a plugin runs inside the server with its rights, so install only code you trust.
Tools from third parties nobody vetted do not belong in a plugin.
Admins see every enabled plugin with its tools and their effects, its pipelines, and its skills under Administration, Plugins.

## Enabling a plugin

An installed plugin loads only once `plugins.enabled` names it, and its options sit under `plugins.config.<name>`:

```toml
[plugins]
enabled = ["lookup"]

[plugins.config.lookup]
url = "https://glossary.example/terms"
```

A secret option is a file like every other secret, here `hivegent.PLUGINS__CONFIG__LOOKUP__API_KEY` under `/run/secrets`.
Hivegent refuses to start when an enabled plugin is missing or fails, when its options do not validate, when options name a plugin that is not enabled or that takes none, and when two contributions share an id, whether a plugin's and a built-in one or those of two plugins.

### NixOS

`services.hivegent.plugins` takes plugin project directories, each holding a `pyproject.toml`, and installs them into the backend's virtualenv:

```nix
services.hivegent = {
  plugins = [ ./hivegent-lookup ];
  settings.plugins = {
    enabled = [ "lookup" ];
    config.lookup.url = "https://glossary.example/terms";
  };
  credentials.PLUGINS__CONFIG__LOOKUP__API_KEY = "/run/secrets/lookup-api-key";
};
```

A plugin runs on the backend's interpreter and uses the dependencies the backend installs, such as `pydantic`, `pydantic-ai`, and `httpx2`.
The build fails when a plugin needs a package the backend lacks or a version it does not hold, and when its modules clash with another package's.

### Container image

The image is built with Nix and has no `pip`, so a plugin is added by building a variant of it, for example in a flake with Hivegent as an input:

```nix
docker =
  let
    inherit (hivegent.packages.x86_64-linux) backend docker;
  in
  docker.override {
    backend = backend.override { plugins = [ ./hivegent-lookup ]; };
  };
```

Build it with `nix build`, load it with `./result | docker load`, and enable the plugin in `config.toml` as above.

## Writing a plugin

A plugin advertises an entry point in the `hivegent.plugins` group, named for the plugin, in its `pyproject.toml`:

```toml
[project]
name = "hivegent-lookup"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["exar", "hivegent-backend>=0,<1"]

[project.entry-points."hivegent.plugins"]
lookup = "hivegent_lookup:plugin"

[build-system]
requires = ["uv-build>=0.11,<1"]
build-backend = "uv_build"
```

The entry point names a function that receives a `Host` and returns a `Plugin`.
A plugin imports only `hivegent.plugins.api` of Hivegent, and exar's tool and ingestion types, such as `effect` and `Retry`, from exar directly.
This one looks up terms in a REST API whose URL the operator configures:

```python
"""A read-only lookup in a REST API as a Hivegent plugin."""

from exar.tools import Retry, effect
from pydantic import BaseModel, HttpUrl, SecretStr

from hivegent.plugins.api import Feature, Host, Localized, Plugin

__all__ = ["plugin"]


class LookupConfig(BaseModel):
    """The operator's `plugins.config.lookup`."""

    url: HttpUrl
    api_key: SecretStr | None = None


def plugin(host: Host) -> Plugin:
    """Build the lookup feature from the operator's config."""
    config = host.config(LookupConfig)
    headers = (
        {}
        if config.api_key is None
        else {"Authorization": f"Bearer {config.api_key.get_secret_value()}"}
    )

    @effect("read")
    async def lookup_term(term: str) -> str:
        """Look up the definition of a company term in the glossary."""
        response = await host.http_client().get(
            str(config.url), params={"term": term}, headers=headers
        )

        if response.is_error:
            raise Retry(f"The glossary answered {response.status_code}.")

        return response.text

    return Plugin(
        features=[
            Feature(
                id="lookup",
                tools=[lookup_term],
                instructions=Localized(
                    en="Look up company terms with `lookup_term` before explaining them.",
                    de="Schlage Firmenbegriffe mit `lookup_term` nach, bevor du sie erklärst.",
                ),
            )
        ]
    )
```

- `host.config(Model)` validates the operator's options with your Pydantic model, unknown keys refused, and a secret belongs in a `SecretStr` field.
- `host.http_client()` is the server's pooled web client, asked for on every call.
  It reaches only the public hosts `security.web_urls` allows, through the egress proxy, as `web_fetch` does, so the operator allows the plugin's endpoint there.
- A tool is a typed async function, and every tool declares its effect with `effect`: `read` changes nothing, `write` changes data in Hivegent, and `execute` performs an action outside Hivegent.
  Running code is no `execute` of itself, the built-in `run_python` is a `read`, as its effects stay in the conversation's `/tmp`.
  Read tools are offered in every mode, while the others are offered only in the write and interactive modes and ask the user before every call.
  The MCP endpoint serves the same tools under the same rules, asking an MCP client's user in its place.
- Feature ids, tool names, converter, chunker, and connector ids, and skill names must be unique among Hivegent's and every enabled plugin's, so choose distinctive ones.
- Raise `Retry` for a refusal the model can correct, the message reaches it.
- `Plugin` also takes `converters` and `chunkers` by id, each a `ConverterPipeline` or `Pipeline` with a label, a description, and a `build` function such as `exar.ingest.offloaded(MyParser)` for a synchronous exar parser or chunker.
  Their options come from `conversion.pipelines.<id>` and `chunking.pipelines.<id>`, which `configure` validates against the fields of the component together with the facts of the `exar.ingest.Host` it has a field for, such as `threads` or `device`.
- `skills` lists read-only directories of global skills on disk as `Path` objects, each folder in them holding a `SKILL.md`, for example `Path(__file__).parent / "skills"`, checked when the plugin loads.
  A global skill of the operator's `skills.dir` with the same name takes precedence.

### Compatibility

`hivegent.plugins.api` is the plugin API and holds only Hivegent's own types.
A plugin imports exar and cbrkit directly, such as `effect` and `Retry` from `exar.tools` or `Parsed`, `offloaded`, and `configure` from `exar.ingest`, and lists them among its dependencies without a version range, so it runs on the versions Hivegent ships.
An exar or cbrkit upgrade of Hivegent that changes these names can therefore break a plugin too.
Hivegent bumps the major version of its `hivegent-backend` distribution whenever the API breaks.
A plugin therefore declares a dependency on `hivegent-backend` with a major version range, as `>=0,<1` above, so that uv and pip refuse to install it beside an incompatible Hivegent.
Hivegent itself checks no version at runtime.

## Writing a connector

A connector reaches an external source, which an operator then declares under `sources.<id>` with its `connector` named for the plugin, so the source is mirrored and indexed like the built-in WebDAV one.
This one mirrors the pages of one space of a document API as markdown:

```python
"""A read-only connector to a document API as a Hivegent plugin."""

from datetime import datetime

from pydantic import BaseModel

from hivegent.plugins.api import Connector, Delta, Host, Plugin, RemoteItem, SourceHost

__all__ = ["plugin"]


class PagesOptions(BaseModel):
    """A source's `options`."""

    space: str


class Pages(Connector):
    """The pages of one space, each a markdown file."""

    def __init__(self, host: SourceHost) -> None:
        self._host = host
        self._space = host.config(PagesOptions).space

    async def delta(self, cursor: str | None, /) -> Delta:
        url = self._host.url.join(f"spaces/{self._space}/pages")
        response = await self._host.http_client().get(url)
        items = [
            RemoteItem(
                id=page["id"],
                path=f"{page['slug']}.md",
                version=page["revision"],
                modified=datetime.fromisoformat(page["updated"]),
            )
            for page in response.raise_for_status().json()
        ]

        return Delta(items=items, listed=True)

    async def read(self, item: RemoteItem, /) -> bytes:
        url = self._host.url.join(f"pages/{item.id}/markdown")

        return (await self._host.http_client().get(url)).raise_for_status().content


def plugin(host: Host) -> Plugin:
    """Contribute the connector as `pages`."""
    return Plugin(connectors={"pages": Pages})
```

```toml
[sources.wiki]
connector = "pages"
url = "https://pages.example.com/api/"
options = { space = "handbook" }
```

- The server builds a connector once per source at startup with a `SourceHost`: `url` is the source's root, `config(Model)` validates its `options`, and `http_client()` is a client authenticated with the source's credentials and confined to its hosts, so a connector never holds a secret and reaches no other host.
- `delta` and `read` are all a connector needs, and the server diffs every listing against what it mirrored, so an `id` must survive a rename, which then moves the document, and a `version` must change with the content.
- `delta(cursor)` returns a `Delta`, every file with `listed=True` for a full listing, after which every other mirrored file is deleted at once, within the source's brake, or only what changed since the cursor it returned last, new or changed `items` and `removed` ones by id or path, which are deleted at once.
- A connector only reads, sources are read-only by design, and a listing the source refused in part names the refused files and folders in `refused`, which then counts as no full listing, rather than leaving them out, since a missing file reads as a deletion.
  The server then mirrors what was listed but deletes nothing merely missing and shows the refused paths as the source's error, while a connector that cannot list the source at all raises.
- Paths are relative and `/`-separated, and the server skips hidden and unsafe ones and holds a sweep of missing files beyond `max_sweep_share` of the mirror for an admin to confirm.

## Testing a plugin

A test installs the plugin by replacing the entry point lookup, so it needs no installed package and no running server:

```python
from importlib.metadata import EntryPoint

import pytest

from hivegent.plugins import loader


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the plugin under its entry point, whatever the environment holds."""

    def found(*, group: str, name: str) -> list[EntryPoint]:
        return [EntryPoint(name, "hivegent_lookup:plugin", group)] if name == "lookup" else []

    monkeypatch.setattr(loader, "entry_points", found)


def test_the_lookup_loads_with_its_config() -> None:
    (lookup,) = loader.load_plugins(
        ["lookup"], {"lookup": {"url": "https://glossary.example/terms"}}
    )
    (feature,) = lookup.plugin.features

    assert [tool.__name__ for tool in feature.tools] == ["lookup_term"]


def test_the_lookup_needs_its_url() -> None:
    with pytest.raises(ValueError, match="failed to load"):
        loader.load_plugins(["lookup"], {"lookup": {}})
```
