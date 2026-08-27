# Einrichtung

Hivegent wird als ein einzelnes Container-Image unter `ghcr.io/dfki-ebls/hivegent` veröffentlicht.
Eine typische Bereitstellung betreibt drei Container mit Docker Compose: die Hivegent-App, eine PostgreSQL-Datenbank und einen OIDC-Identitätsanbieter.
Die Datei `compose.yaml` im Repository ist ein vollständiges, sofort lauffähiges Beispiel, das Rauthy als Anbieter verwendet.

## Voraussetzungen

- Docker mit dem Compose-Plugin (Podman funktioniert ebenfalls).
- Ein OpenAI-kompatibler Sprachmodell-Endpunkt, entweder eine gehostete API wie OpenAI oder ein lokaler Server wie vLLM, SGLang oder Ollama.
- Optional: eine NVIDIA-GPU für schnellere Dokumentumwandlung und OCR.

## Schnellstart

1. Kopieren Sie `compose.yaml` aus dem Repository in ein leeres Verzeichnis.
2. Passen Sie die Konfiguration wie unten beschrieben an, insbesondere das Sprachmodell und den OIDC-Aussteller.
3. Starten Sie den Stapel:

```bash
docker compose up -d
```

4. Der erste Start lädt die Images und initialisiert die Datenbank, was einige Minuten dauern kann.
5. Öffnen Sie <http://localhost:8080> im Browser.

## Konfiguration

Das Backend liest eine TOML-Konfigurationsdatei (eingebunden unter `/data/config.toml`) und akzeptiert zusätzlich `HIVEGENT_*`-Umgebungsvariablen, die einzelne Schlüssel überschreiben.
Verschachtelte Schlüssel verwenden einen doppelten Unterstrich, aus `[llm] model` wird also `HIVEGENT_LLM__MODEL`.

Eine minimale Konfiguration sieht so aus:

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

Die wichtigsten Einstellungen:

- `llm.model`: das Hauptmodell für den Chat, das ein großes Kontextfenster und Werkzeugaufrufe benötigt.
- `llm.aux_model`: ein kleines, schnelles, vision-fähiges Modell für Dokumentumwandlung, Bildbeschreibungen und Titel. Ohne Angabe wird auf das Hauptmodell zurückgegriffen.
- `llm.base_url` und `llm.api_key`: Ihr OpenAI-kompatibler Endpunkt.
- `llm.inference_provider`: die Endpunktimplementierung, entweder `llama.cpp`, `vllm` oder `openai`. Voreingestellt ist `openai`, womit nur standardisierte Felder gesendet werden. Setzen Sie den Wert passend zu Ihrem Server, sonst bleiben die Steuerung des Reasonings und die Korrekturen für selbst gehostete Chat-Templates aus.
- `db.url`: die PostgreSQL-Verbindungszeichenfolge. Die mitgelieferte Datenbank hat pgvector bereits aktiviert.
- `auth.issuer`: die Aussteller-URL des OIDC-Anbieters.
- `auth.audience`: die akzeptierten Token-Zielgruppen. Der Eintrag `hivegent-*` akzeptiert jeden aktuellen und künftigen Hivegent-Client.

Häufige Ergänzungen sind `HIVEGENT_TOOLS__ENABLE_WEB=true` für die Websuche, `HIVEGENT_EMBEDDING__MODEL` zum Wechseln des Embedding-Modells und `HIVEGENT_MCP__ENABLE=true` zum Bereitstellen des MCP-Endpunkts.

`tools.excluded` hält einzelne Werkzeuge vom Modell fern, benannt so, wie die Admin-Werkzeugkonsole sie auflistet (`GET /api/debug/tools`).
Voreingestellt ist `["jq", "list_conversations", "get_conversation"]`, was zu einer Installation passt, deren Dokumente Dokumente sind und kein JSON.
Jedes hier nicht genannte Werkzeug erreicht das Modell bei jeder Anfrage, diese Liste ist also der Ort, an dem Sie eine Fähigkeit gegen den Kontext ihres Schemas abwägen.
Wird ein Werkzeug genannt, das es nicht gibt, startet der Server nicht, statt stillschweigend nichts auszuschließen.

Von Benutzern angegebene LLM- und MCP-Server-URLs bleiben deaktiviert, bis ihre Hosts unter `[security.user_urls] allow_hosts` aufgeführt sind.
Ein Eintrag erlaubt diese Domain und ihre Subdomains, eine leere Liste verweigert jeden Host, und `"*"` erlaubt jeden öffentlichen Host.
Diese Anfragen und alle vom Modell gesteuerten Web-Anfragen laufen durch den mitgelieferten Smokescreen-Proxy, der private und reservierte Ziele nach der DNS-Auflösung ablehnt.
Vom Betreiber konfigurierte URLs wie `llm.base_url` bleiben direkt und vertrauenswürdig.

## Identitätsanbieter (OIDC)

Hivegent überlässt die Anmeldung einem OIDC-Anbieter und ist anbieterunabhängig.
Das Beispiel verwendet Rauthy, aber Keycloak, Authentik, Auth0 und andere funktionieren genauso.
Der Browser liest Aussteller und Client-ID zur Laufzeit vom Backend, ein Anbieterwechsel erfordert daher keinen Neubau.

Für das mitgelieferte Rauthy-Beispiel:

1. Lesen Sie nach dem ersten Start das einmalige Administrator-Passwort aus den Protokollen mit `docker compose logs rauthy`.
2. Melden Sie sich an der Rauthy-Administrationsoberfläche unter <http://auth.localhost:8081> an.
3. Legen Sie einen öffentlichen Client mit der ID `hivegent-spa` an, der PKCE mit S256 nutzt, und setzen Sie sowohl die Redirect-URI als auch den erlaubten Ursprung auf `http://localhost:8080`.
4. Legen Sie Ihre Benutzer und optional Gruppen in Rauthy an.

### Gruppen und Rollen

Die Zugriffssteuerung richtet sich nach den Claims im Token jedes Benutzers.

- Jeder Benutzer hat einen privaten Arbeitsbereich, den nur er lesen und schreiben kann.
- Gruppenmitgliedschaften stammen aus dem Gruppen-Claim, standardmäßig `groups`. Ein Eintrag wie `engineering` gewährt Zugriff auf den geteilten Arbeitsbereich dieser Gruppe, und ein Zusatz legt die Berechtigung fest, etwa `engineering:read` oder `engineering:write`.
- Die feste Rolle `admin`, gelesen aus dem Rollen-Claim (standardmäßig `roles`), gewährt Administratoraktionen wie den Wartungsmodus und das Zurücksetzen von Daten.

Konfigurieren Sie Ihren Anbieter so, dass diese Claims im Access-Token enthalten sind.

### Gruppenidentität und Umbenennen

Eine Gruppe wird über die Kennung identifiziert, die ihr Gruppen-Claim enthält, und der Claim kann einen Eintrag auf zwei Arten schreiben.

Ein Objekt nach RFC 9068, das jeden Eintrag als SCIM-Form `{"value": "<id>", "display": "<name>"}` kodiert, führt Kennung und Anzeigename getrennt.
Die Kennung adressiert die Gruppe in jedem Pfad (`@<id>/notes.md`), während der Anzeigename nur als Beschriftung in der Oberfläche dient, daher erhält das Umbenennen einer solchen Gruppe ihren geteilten Arbeitsbereich.
Anbieter, die reine Kennungen ausgeben wie Entra ID, und solche mit einem Claim-Mapper, der auf die Gruppen-ID zeigen kann wie Keycloak, Authentik, Okta und Auth0, fallen ebenfalls hierunter.

Eine reine Zeichenkette ist alles, was manche Anbieter, darunter Rauthy, ausgeben können.
Dann gibt es keine separate Kennung, der Name ist die Kennung und erscheint direkt in Pfaden.
Das funktioniert, aber beim Umbenennen einer solchen Gruppe bleiben ihre Dokumente unter dem alten Namen zurück, da der neue Name nicht von einer neu angelegten Gruppe zu unterscheiden ist.
Konfigurieren Sie Ihren Anbieter auf die Objektform, wenn Umbenennungen sicher sein sollen.

## Aktualisieren

```bash
docker compose pull
docker compose up -d
```

Das Backend wendet seine Datenbankmigrationen beim Start automatisch an, ein separater Schritt ist nicht nötig.

## Hinweise für den Produktivbetrieb

- Die Beispiel-Geheimnisse, Verschlüsselungsschlüssel und Passwörter sind nur für lokale Tests gedacht. Erzeugen Sie für jeden echten Einsatz eigene Werte.
- Stellen Sie den Stapel hinter HTTPS, etwa indem Sie dem mitgelieferten Proxy eine echte Domain geben oder einen eigenen Reverse-Proxy davorschalten.
- Für GPU-beschleunigte Dokumentverarbeitung entfernen Sie die Kommentarzeichen am NVIDIA-Runtime-Abschnitt in `compose.yaml`.
