# Einrichtung

Hivegent wird als ein Container-Image veröffentlicht, `ghcr.io/dfki-ebls/hivegent`.
Eine typische Bereitstellung betreibt drei Container mit Docker Compose: Hivegent, PostgreSQL und einen OIDC-Identitätsanbieter.
Die Datei `compose.yaml` im Repository ist ein sofort lauffähiges Beispiel mit Rauthy.

> **Ergänzende Nutzungsbedingungen:** Hivegent wird unter der MIT-Lizenz und den [Ergänzenden Nutzungsbedingungen](terms.md) des DFKI bereitgestellt.
> Mit dem Herunterladen, Installieren oder Verwenden der Software erkennen Sie die Ergänzenden Nutzungsbedingungen an.
> Bitte lesen Sie diese vorher vollständig.

## Voraussetzungen

- Docker mit Compose oder Podman.
- Ein OpenAI-kompatibler Modell-Endpunkt, gehostet (etwa OpenAI) oder lokal (etwa vLLM, SGLang oder Ollama).
- Optional: eine NVIDIA-GPU für schnellere Umwandlung und OCR.

## Schnellstart

1. Kopieren Sie `compose.yaml` in ein leeres Verzeichnis.
2. Lesen Sie die [Ergänzenden Nutzungsbedingungen](terms.md) und erkennen Sie diese an, indem Sie `HIVEGENT_ACCEPT_TERMS` auf `true` setzen, andernfalls startet Hivegent nicht.
3. Setzen Sie Sprachmodell und OIDC-Aussteller wie unten beschrieben.
4. Starten Sie mit `docker compose up -d`, der erste Start dauert einige Minuten.
5. Öffnen Sie <http://localhost:8080>.

## Konfiguration

Das Backend liest `/data/config.toml` und `HIVEGENT_*`-Umgebungsvariablen, die Vorrang haben.
Verschachtelte Schlüssel verwenden einen doppelten Unterstrich, aus `[llm] model` wird `HIVEGENT_LLM__MODEL`.
Binden Sie Geheimnisse wie API-Schlüssel als Dateien `hivegent.<NAME>` für die Variable `HIVEGENT_<NAME>` unter `/run/secrets` ein, etwa `hivegent.LLM__API_KEY` mit Compose `secrets`.

```toml
[db]
url = "postgresql+psycopg://hivegent:hivegent@postgresql:5432/hivegent"

[llm]
model = "ihr-chat-modell"
aux_model = "ihr-kleines-vision-modell"
base_url = "http://ihr-llm-host:8000/v1"
inference_provider = "vllm"
# api_key = "..."   # nur falls Ihr Anbieter einen verlangt

[auth]
issuer = "http://auth.localhost:8081/auth/v1"
audience = ["hivegent-*"]
```

- `llm.model`: das Chat-Modell, das ein großes Kontextfenster und Werkzeugaufrufe braucht.
- `llm.aux_model`: ein kleines, schnelles, vision-fähiges Modell für Umwandlung, Bildbeschreibungen und Titel.
  Standard ist das Chat-Modell.
- `llm.base_url` und `llm.api_key`: Ihr Endpunkt.
- `llm.inference_provider`: `llama.cpp`, `vllm` oder `openai` (Standard).
  Passend zum Server gesetzt, aktiviert er die Steuerung des Reasonings und Korrekturen für Chat-Templates.
- `db.url`: die PostgreSQL-Verbindungszeichenfolge.
  Die mitgelieferte Datenbank hat pgvector bereits aktiviert.
- `auth.issuer`: die Aussteller-URL des OIDC-Anbieters.
- `auth.audience`: die akzeptierten Token-Zielgruppen.
  `hivegent-*` akzeptiert jeden Hivegent-Client.

Häufige Ergänzungen sind `HIVEGENT_TOOLS__SEARCH_URL` für die Websuche, `HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS='["*"]'` für den Web-Abruf, `HIVEGENT_EMBEDDING__MODEL` für das Embedding-Modell und `HIVEGENT_MCP__ENABLE=true` für den MCP-Endpunkt.

`tools.disabled` hält Werkzeuge vom Modell fern, benannt wie in `GET /api/debug/tools`.
Voreingestellt ist `["list_conversations", "get_conversation"]`, da Unteragenten frühere Unterhaltungen bereits erreichen.
Ein unbekannter Name verhindert den Serverstart.

Unterhaltungsdateien (`/tmp` und vorgemerkte Änderungen) werden nach `conversations.ttl_hours` ohne Aktivität gelöscht (Standard 72), einstellbar mit `HIVEGENT_CONVERSATIONS__TTL_HOURS`.

Von Benutzern angegebene LLM- und MCP-URLs bleiben deaktiviert, bis ihre Hosts unter `[security.user_urls] allow_hosts` stehen.
Ein Eintrag erlaubt die Domain und ihre Subdomains, `"*"` erlaubt jeden öffentlichen Host.
Diese Anfragen und alle vom Modell gesteuerten Web-Anfragen laufen durch den mitgelieferten Smokescreen-Proxy, der private und reservierte Adressen ablehnt.
Vom Betreiber konfigurierte URLs wie `llm.base_url` verbinden sich direkt.

Die Websuche braucht einen Metasuchdienst, den Hivegent nicht mitliefert, so wie es auch PostgreSQL Ihnen überlässt.
SearXNG ist das voreingestellte Backend: `compose.yaml` startet dessen offizielles Image, und die NixOS-Option `services.hivegent.searxng.createLocally` bindet das `services.searx` des Hosts ein.
OpenSERP funktioniert ebenfalls mit `tools.search_backend = "openserp"`, durchsucht allerdings nur das allgemeine Web und meldet kein Veröffentlichungsdatum.
Setzen Sie `tools.search_url` auf den Dienst, sonst bleibt `web_search` verborgen, und tragen Sie bei SearXNG `json` unter `search.formats` ein.
`web_fetch` wird getrennt über `[security.web_urls] allow_hosts` aktiviert, wobei `"*"` jede öffentliche Seite erlaubt, die der Benutzer oder eine Suche genannt hat, und eine Liste von Domains den Abruf auf diese beschränkt.
Eine URL, die das Modell selbst schreibt, wird abgelehnt, damit auf einer Seite platzierte Anweisungen keine Gesprächsdaten in einem Link hinausschleusen können.

Um einen Betreiber-Endpunkt über einen Unix-Socket zu erreichen, ordnen Sie seinen Hostnamen unter `[network.unix_sockets]` zu, etwa mit `HIVEGENT_NETWORK__UNIX_SOCKETS='{"llmhop": "/run/llmhop/default.sock"}'`.
`llm.base_url = "http://llmhop/v1"` verbindet sich dann mit dem Socket.
Von Benutzern angegebene URLs nutzen diese Zuordnung nie.
Das NixOS-Modul läuft als dynamischer Benutzer, geben Sie dem Socket daher eine eigene statische Gruppe (nicht `hivegent`) mit Modus `0660` und fügen Sie sie zu `systemd.services.hivegent.serviceConfig.SupplementaryGroups` hinzu.

## Identitätsanbieter (OIDC)

Hivegent funktioniert mit jedem OIDC-Anbieter, etwa Rauthy, Keycloak, Authentik oder Auth0.
Der Browser liest Aussteller und Client-ID zur Laufzeit vom Backend, ein Neubau ist nicht nötig.

Für das mitgelieferte Rauthy:

1. Lesen Sie das einmalige Administrator-Passwort mit `docker compose logs rauthy`.
2. Melden Sie sich unter <http://auth.localhost:8081> an.
3. Legen Sie einen öffentlichen Client `hivegent-spa` mit PKCE (S256) an und setzen Sie Redirect-URI und erlaubten Ursprung auf `http://localhost:8080`.
4. Legen Sie Benutzer und optional Gruppen an.

### Gruppen und Rollen

Der Zugriff richtet sich nach den Token-Claims, die Ihr Anbieter in das Access-Token aufnehmen muss.

- Jeder Benutzer hat einen privaten Arbeitsbereich.
- Gruppen stammen aus dem Claim `groups` oder `custom.groups` (`claims.groups`).
  Ein Eintrag wie `engineering` gewährt den geteilten Arbeitsbereich, `engineering:read` oder `engineering:write` legt die Berechtigung fest.
- Die Rolle `admin` aus dem Claim `roles` oder `custom.roles` gewährt Administratoraktionen wie Wartungsmodus und Zurücksetzen von Daten.

### Gruppenidentität und Umbenennen

Ein Eintrag im Gruppen-Claim ist entweder ein Objekt oder eine reine Zeichenkette.

Ein Objekt nach RFC 9068, `{"value": "<id>", "display": "<name>"}`, trennt Kennung und Anzeigename.
Die Kennung erscheint in Pfaden (`@<id>/notes.md`), daher bleibt der Arbeitsbereich beim Umbenennen erhalten.
So arbeiten Entra ID sowie Keycloak, Authentik, Okta oder Auth0 mit einem Claim-Mapper auf die Gruppen-ID.

Eine reine Zeichenkette, mehr kann Rauthy nicht ausgeben, ist Name und Kennung zugleich.
Beim Umbenennen einer solchen Gruppe bleiben ihre Dokumente unter dem alten Namen zurück.

## Aktualisieren

```bash
docker compose pull
docker compose up -d
```

Datenbankmigrationen laufen beim Start automatisch.

## Hinweise für den Produktivbetrieb

- Ersetzen Sie die Beispiel-Geheimnisse, Schlüssel und Passwörter.
- Betreiben Sie den Stapel über HTTPS, mit einer echten Domain für den mitgelieferten Proxy oder einem eigenen Reverse-Proxy.
- Für GPU-Verarbeitung entfernen Sie die Kommentarzeichen am NVIDIA-Runtime-Abschnitt in `compose.yaml`.
