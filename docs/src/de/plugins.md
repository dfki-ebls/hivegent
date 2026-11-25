# Plugins

Ein Plugin ist ein Python-Paket, das Hivegent Tools, Dokumentkonverter, Chunker, Connectors zu externen Quellen und Skill-Verzeichnisse hinzufügt.
Administratoren installieren und aktivieren Plugins, und ein Plugin läuft im Server mit dessen Rechten, installieren Sie also nur Code, dem Sie vertrauen.
Tools von Dritten, die niemand geprüft hat, gehören nicht in ein Plugin.
Admins sehen jedes aktivierte Plugin mit seinen Tools und deren Wirkung, seinen Pipelines und seinen Skills unter Administration, Plugins.

## Ein Plugin aktivieren

Ein installiertes Plugin wird erst geladen, wenn `plugins.enabled` es nennt, und seine Optionen stehen unter `plugins.config.<name>`:

```toml
[plugins]
enabled = ["lookup"]

[plugins.config.lookup]
url = "https://glossary.example/terms"
```

Eine geheime Option ist eine Datei wie jedes andere Geheimnis, hier `hivegent.PLUGINS__CONFIG__LOOKUP__API_KEY` unter `/run/secrets`.
Hivegent startet nicht, wenn ein aktiviertes Plugin fehlt oder scheitert, wenn seine Optionen ungültig sind, wenn Optionen ein Plugin nennen, das nicht aktiviert ist oder keine annimmt, und wenn zwei Beiträge dieselbe Kennung tragen, ob die eines Plugins und eine eingebaute oder die zweier Plugins.

### NixOS

`services.hivegent.plugins` nimmt Projektverzeichnisse von Plugins mit je einer `pyproject.toml` und installiert sie in die virtuelle Umgebung des Backends:

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

Ein Plugin läuft auf dem Interpreter des Backends und nutzt die Abhängigkeiten, die das Backend installiert, etwa `pydantic`, `pydantic-ai` und `httpx2`.
Der Build scheitert, wenn ein Plugin ein Paket braucht, das dem Backend fehlt, oder eine Version, die es nicht hat, und wenn seine Module mit denen eines anderen Pakets kollidieren.

### Container-Image

Das Image wird mit Nix gebaut und enthält kein `pip`, ein Plugin kommt daher über eine eigene Variante hinzu, etwa in einem Flake mit Hivegent als Input:

```nix
docker =
  let
    inherit (hivegent.packages.x86_64-linux) backend docker;
  in
  docker.override {
    backend = backend.override { plugins = [ ./hivegent-lookup ]; };
  };
```

Bauen Sie es mit `nix build`, laden Sie es mit `./result | docker load` und aktivieren Sie das Plugin wie oben in `config.toml`.

## Ein Plugin schreiben

Ein Plugin meldet in seiner `pyproject.toml` einen Entry Point in der Gruppe `hivegent.plugins` an, benannt nach dem Plugin:

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

Der Entry Point nennt eine Funktion, die einen `Host` erhält und ein `Plugin` zurückgibt.
Ein Plugin importiert von Hivegent nur `hivegent.plugins.api`, die Tool- und Ingestion-Typen von exar wie `effect` und `Retry` dagegen direkt aus exar.
Dieses schlägt Begriffe in einer REST-API nach, deren URL der Betreiber festlegt:

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

- `host.config(Model)` prüft die Optionen des Betreibers mit Ihrem Pydantic-Modell, unbekannte Schlüssel werden abgelehnt, und ein Geheimnis gehört in ein Feld vom Typ `SecretStr`.
- `host.http_client()` ist der gemeinsame Web-Client des Servers, bei jedem Aufruf neu abgefragt.
  Er erreicht über den Egress-Proxy nur die öffentlichen Hosts, die `security.web_urls` erlaubt, wie `web_fetch`, also erlaubt der Betreiber dort den Endpunkt des Plugins.
- Ein Tool ist eine typisierte asynchrone Funktion, und jedes Tool gibt mit `effect` seine Wirkung an: `read` ändert nichts, `write` ändert Daten in Hivegent und `execute` führt eine Aktion außerhalb von Hivegent aus.
  Code auszuführen ist für sich kein `execute`, das eingebaute `run_python` ist ein `read`, da seine Wirkungen im `/tmp` der Unterhaltung bleiben.
  Lesende Tools stehen in jedem Modus bereit, die anderen nur im Schreib- und im interaktiven Modus, und sie fragen vor jedem Aufruf nach.
  Der MCP-Endpunkt bietet dieselben Tools nach denselben Regeln an und fragt dort den Nutzer des MCP-Clients.
- Feature-Kennungen, Tool-Namen, Kennungen von Konvertern, Chunkern und Connectors sowie Skill-Namen müssen unter denen von Hivegent und aller aktivierten Plugins eindeutig sein, wählen Sie also unverwechselbare.
- Lösen Sie `Retry` für eine Ablehnung aus, die das Modell beheben kann, die Nachricht erreicht es.
- `Plugin` nimmt auch `converters` und `chunkers` nach Kennung an, je eine `ConverterPipeline` oder `Pipeline` mit Bezeichnung, Beschreibung und einer `build`-Funktion wie `exar.ingest.offloaded(MyParser)` für einen synchronen Parser oder Chunker von exar.
  Ihre Optionen kommen aus `conversion.pipelines.<id>` und `chunking.pipelines.<id>`, die `configure` gegen die Felder der Komponente prüft, zusammen mit den Angaben des `exar.ingest.Host`, für die sie ein Feld hat, etwa `threads` oder `device`.
- `skills` nennt schreibgeschützte Verzeichnisse globaler Skills auf der Festplatte als `Path`-Objekte, deren Ordner je eine `SKILL.md` enthalten, etwa `Path(__file__).parent / "skills"`, geprüft beim Laden des Plugins.
  Ein gleichnamiger globaler Skill aus dem `skills.dir` des Betreibers hat Vorrang.

### Kompatibilität

`hivegent.plugins.api` ist die Plugin-API und enthält nur die eigenen Typen von Hivegent.
Ein Plugin importiert exar und cbrkit direkt, etwa `effect` und `Retry` aus `exar.tools` oder `Parsed`, `offloaded` und `configure` aus `exar.ingest`, und führt sie ohne Versionsbereich unter seinen Abhängigkeiten, sodass es mit den Versionen läuft, die Hivegent mitbringt.
Ein Update von exar oder cbrkit in Hivegent, das diese Namen ändert, kann ein Plugin daher ebenfalls brechen.
Hivegent erhöht die Hauptversion seiner Distribution `hivegent-backend`, sobald die API bricht.
Ein Plugin gibt deshalb eine Abhängigkeit auf `hivegent-backend` mit einem Bereich der Hauptversion an, wie `>=0,<1` oben, damit uv und pip es nicht neben einem inkompatiblen Hivegent installieren.
Hivegent selbst prüft zur Laufzeit keine Version.

## Einen Connector schreiben

Ein Connector erreicht eine externe Quelle, die der Betrieb dann unter `sources.<id>` anlegt, mit dem `connector` nach dem Plugin benannt, sodass die Quelle wie die eingebaute WebDAV-Quelle gespiegelt und indexiert wird.
Dieser spiegelt die Seiten eines Bereichs einer Dokument-API als Markdown:

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

- Der Server baut einen Connector beim Start einmal je Quelle mit einem `SourceHost`: `url` ist die Wurzel der Quelle, `config(Model)` prüft ihre `options`, und `http_client()` ist ein mit den Zugangsdaten der Quelle angemeldeter und auf ihre Hosts beschränkter Client, sodass ein Connector nie ein Geheimnis hält und keinen anderen Host erreicht.
- `delta` und `read` genügen, und der Server vergleicht jede Auflistung mit dem Gespiegelten, daher muss eine `id` ein Umbenennen überstehen, das dann das Dokument verschiebt, und eine `version` sich mit dem Inhalt ändern.
- `delta(cursor)` liefert ein `Delta`, jede Datei mit `listed=True` für eine vollständige Auflistung, nach der jede andere gespiegelte Datei sofort gelöscht wird, im Rahmen der Bremse der Quelle, oder nur, was sich seit dem zuletzt gelieferten Cursor geändert hat, neue oder geänderte `items` und `removed` nach Kennung oder Pfad, die sofort gelöscht werden.
- Ein Connector liest nur, Quellen sind grundsätzlich schreibgeschützt, und eine Auflistung, die die Quelle teilweise verweigerte, nennt die verweigerten Dateien und Ordner in `refused`, die dann nicht als vollständige Auflistung gilt, statt sie auszulassen, da eine fehlende Datei als Löschung gilt.
  Der Server spiegelt dann, was gelistet wurde, löscht aber nichts, das nur fehlt, und zeigt die verweigerten Pfade als Fehler der Quelle, während ein Connector, der die Quelle gar nicht auflisten kann, einen Fehler wirft.
- Pfade sind relativ und durch `/` getrennt, der Server überspringt versteckte und unsichere und hält ein Aufräumen fehlender Dateien über `max_sweep_share` des Spiegels zurück, bis ein Admin es bestätigt.

## Ein Plugin testen

Ein Test installiert das Plugin, indem er die Suche nach Entry Points ersetzt, und braucht so weder ein installiertes Paket noch einen laufenden Server:

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
