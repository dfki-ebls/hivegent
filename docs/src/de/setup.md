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

## Schnellstart

1. Kopieren Sie `compose.yaml` in ein leeres Verzeichnis.
2. Lesen Sie die [Ergänzenden Nutzungsbedingungen](terms.md) und erkennen Sie diese an, indem Sie `HIVEGENT_ACCEPT_TERMS` auf `true` setzen, andernfalls startet Hivegent nicht.
3. Setzen Sie Sprachmodell und OIDC-Aussteller wie unten beschrieben.
4. Starten Sie mit `docker compose up -d`, der erste Start dauert einige Minuten.
5. Öffnen Sie <http://localhost:8080>.

## Konfiguration

Das Backend liest `/data/config.toml`, die der Container eingebunden braucht, und `HIVEGENT_*`-Umgebungsvariablen, die Vorrang haben.
Verschachtelte Schlüssel verwenden einen doppelten Unterstrich, aus `[llm] default` wird `HIVEGENT_LLM__DEFAULT`.
Binden Sie Geheimnisse wie API-Schlüssel als Dateien `hivegent.<NAME>` für die Variable `HIVEGENT_<NAME>` unter `/run/secrets` ein, etwa `hivegent.LLM__PROVIDERS__LOCAL__API_KEY` mit Compose `secrets`.

```toml
[db]
url = "postgresql+psycopg://hivegent:hivegent@postgresql:5432/hivegent"

[llm]
default = "chat"
aux = "klein"

[llm.providers.local]
base_url = "http://ihr-llm-host:8000/v1"
inference_provider = "vllm"

[llm.models.chat]
provider = "local"
model = "ihr-chat-modell"
label = "Chat"
reasoning = ["low", "medium", "high"]
vision = true

[llm.models.klein]
provider = "local"
model = "ihr-kleines-vision-modell"
vision = true
chat = false

[auth]
issuer = "http://auth.localhost:8081/auth/v1/"
audience = ["hivegent-spa", "hivegent-bot"]
service_client_id = "hivegent-bot"
```

- `llm.providers.<id>`: ein Endpunkt, seine `base_url` und sein `inference_provider`, also `llama.cpp`, `vllm`, `sglang`, `ollama`, `openai` (Standard) für OpenAIs eigene API oder `openai-compatible` für jeden anderen Server.
  Passend zum Server gesetzt, aktiviert er die Steuerung des Reasonings und Korrekturen für Chat-Templates und verhindert, dass ein lokales Modell wie ein OpenAI-Modell bepreist oder tokenisiert wird.
  llama.cpp und vLLM begrenzen das Reasoning auf `llm.thinking_budget_tokens`, SGLang und Ollama schalten es nur ein und aus.
  Sein API-Schlüssel, falls er einen braucht, ist das Geheimnis `hivegent.LLM__PROVIDERS__<ID>__API_KEY`.
- `llm.models.<id>`: ein Modell, das sein `provider` als `model` bereitstellt, angezeigt als `label`.
  `reasoning` listet die Denkstufen, die das Modell umsetzt und unter denen Nutzer wählen, lassen Sie es daher für ein Modell, das jede Stufe annimmt, ungesetzt, listen Sie die Stufen eines Modells, das nur einige umsetzt, und setzen Sie `[]` für eines, das nicht denkt, was die Auswahl verbirgt und das Denken abschaltet, `vision` erlaubt Bildanhänge, und `max_tokens` und `context_window` geben seine Grenzen an.
  `max_images` gibt an, wie viele Bilder sein Server höchstens in einer Anfrage annimmt, etwa vLLMs `--limit-mm-per-prompt`, und ist standardmäßig `multimodal.max_images`, das ohne Wert unbegrenzt ist.
  Darüber hinaus lässt eine Anfrage ältere Bilder des Gesprächs weg, und eine Nachricht kann nicht mehr anhängen.
  `reasoning_overrides` sendet für eine gewählte Denkstufe eine andere, etwa `{ high = "xhigh" }` für Qwen3.8, das `xhigh` anstelle von `high` umsetzt.
  `chat = false` hält ein Modell aus dem Chat heraus, etwa eines nur für eine der folgenden Rollen.
  Bei einem Anbieter `llama.cpp`, `sglang` oder `openai-compatible` nennt `family` die Modellfamilie für ihre Eigenheiten bei Werkzeugen und Schemas: `llama`, `gemma`, `qwen`, `deepseek`, `mistral`, `cohere`, `gpt-oss` oder `glm`.
  Ohne sie wählt ein bereitgestellter Name wie `Qwen/Qwen3-32B` die Familie seines Herstellers, während vLLM und Ollama die Familie selbst am Namen erkennen und `family` ablehnen.
- `llm.default`: das Chat-Modell, mit dem eine Unterhaltung beginnt, das ein großes Kontextfenster und Werkzeugaufrufe braucht.
  Gibt es mehrere Chat-Modelle, wählen Nutzer zwischen ihnen.
- `llm.aux`: ein kleines, schnelles, vision-fähiges Modell für Umwandlung, Bildbeschreibungen und Titel, immer ohne Denken.
  Alles, was dem Nutzer antwortet, darunter Unteragenten, Kompaktierung, Fallbasen und das `complete` der Sandbox, läuft auf dem Chat-Modell mit dem gewählten Denkaufwand.
  Standard ist `llm.default`.
- `llm.stt`: ein Transkriptionsmodell wie `whisper-1` für Browser ohne Spracherkennung, aus, solange es nicht gesetzt ist.
- `subagents.max_cost`: höchstens so viele US-Dollar darf ein Chat-Zug samt seiner Unteragenten ausgeben, unbegrenzt, solange es nicht gesetzt ist.
  Es zählt nur Modelle, die pydantic-ai bepreisen kann, daher begrenzen ein selbst betriebenes allein `llm.request_limit` und die Token-Grenzen.
- `embedding.endpoint`, `rerank.endpoint` und `transparency.endpoint`: der Anbieter, über den ein entfernter Embedder (`embedding.provider = "openai"`), Reranker (`rerank.provider = "http"`, dessen Route `/rerank`) oder Wasserzeichendetektor (unter `transparency.detector_path`) läuft, mit dessen Schlüssel.
  Ein Pfad wird wie ein Link gegen die `base_url` des Anbieters aufgelöst, relativ darunter, wie `rerank`, und absolut auf deren Origin, wie `/llmhop/proxy/watermark/detect` von llmhop.
  Ein entfernter Embedder braucht außerdem `model` und dessen Ausgabegröße `dimension`.
  `embedding.query_prefix` und `embedding.document_prefix` stellen voran, was das Modell erwartet, etwa `"query: "` und `"passage: "` für e5-Modelle, oder eine Anweisung wie `"Instruct: Given a question, retrieve passages that answer it\nQuery:"` vor den Anfragen von Qwen3-Embedding-Modellen.
  `embedding.request` geht an jede Anfrage, etwa `extra_body = { truncate_prompt_tokens = -1 }`, womit vLLM zu lange Texte selbst kürzt.
  Nur OpenAIs eigene Modelle werden clientseitig auf ihre 8192 Tokens gekürzt, da nur ihr Tokenizer bekannt ist, daher muss jeder andere Server zu lange Texte selbst kürzen oder lehnt sie ab.
- `db.url`: die PostgreSQL-Verbindungszeichenfolge.
  Die mitgelieferte Datenbank hat pgvector bereits aktiviert.
- `db.pool_size`, `db.max_overflow` und `db.pool_timeout_seconds`: die Verbindungen, die ein Server offen hält (Standard 10), die, die er unter Last zusätzlich öffnet (Standard 10), und wie lange eine Anfrage auf eine wartet, bevor sie scheitert (Standard 30 Sekunden).
  Die Sperren des Servers halten eigene Verbindungen außerhalb des Pools, bemessen Sie `max_connections` von PostgreSQL daher für `pool_size + max_overflow` plus etwa 2 je Server.
- `auth.issuer`: die Aussteller-URL des OIDC-Anbieters, ohne die `hivegent serve` nicht startet, über `https` bereitgestellt, außer ihr Host ist `localhost`, eine Subdomain davon, `127.0.0.1` oder `::1`.
  Sie muss den Aussteller im Discovery-Dokument des Anbieters nennen, bis auf einen abschließenden Schrägstrich, dessen genaue Schreibweise dann das `iss` der Tokens tragen muss.
  Nur `hivegent dev`, der lokale Entwicklungsserver, läuft ohne sie, lauscht auf der Loopback-Adresse und handelt bei jeder Anfrage als Entwicklungsnutzer.
- `auth.audience`: die akzeptierten Token-Zielgruppen, die genauen IDs der Clients, die die API aufrufen dürfen, etwa der SPA und des Bridge-Bots.
- `auth.service_client_id`: der Client der Teams-Bridge, dessen Tokens nur den globalen Arbeitsbereich lesen, nur im Lesemodus, ohne Erinnerungs- und Unterhaltungs-Tools, und nur die Chat-Route erreichen.
  Die Bridge startet nur, wenn das Backend ihr Token als Dienstprinzipal ausweist, und antwortet nur den Teams-Mandanten, die ihr `teams.allowedTenants` nennt (`bridge/README.md`).

Häufige Ergänzungen sind `HIVEGENT_TOOLS__SEARCH_URL` für die Websuche, `HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS='["*"]'` für den Web-Abruf, `HIVEGENT_EMBEDDING__MODEL` für das Embedding-Modell und `HIVEGENT_MCP__BASE_URL`, die öffentliche URL des Endpunkts, deren Pfad genau `/mcp` sein muss, etwa `https://hivegent.example.com/mcp`, für den MCP-Endpunkt, der das Secret `hivegent.MCP__CONFIRMATION_KEY` braucht, eine Zufallszeichenkette wie `openssl rand -hex 32`, die die Bestätigungen seiner Clients signiert, und dessen Standardmodus `proxy` außerdem `HIVEGENT_MCP__CLIENT_ID` und das Secret `hivegent.MCP__CLIENT_SECRET` braucht.
`HIVEGENT_DOCS_URL` nennt, wo dieses Handbuch bereitgestellt wird, etwa `/docs/`, und zeigt in der App einen Link darauf, das NixOS-Modul und der Container setzen es dorthin, wo sie das Handbuch bereitstellen, sofern sie eines mitliefern.
Im Container lenkt `-e HIVEGENT_DOCS_URL=https://docs.example.com/` den Link auf ein anderswo bereitgestelltes Handbuch.

`tools.disabled` hält Werkzeuge vom Modell fern, benannt wie in `GET /api/v1/tools`.
Voreingestellt ist die leere Liste, und ein zurückgehaltenes Werkzeug bleibt auch den Unteragenten verwehrt, sodass die Gesprächswerkzeuge zu nennen auch den Unteragenten `conversations` abschaltet.
Ein unbekannter Name verhindert den Serverstart mit einem Fehler, der die gültigen Namen aufzählt.

`HIVEGENT_RAGOLD__URL` gibt an, wo eine RAGold-Instanz läuft, wodurch Nutzer eine Antwort mit ihren Dokumenten als Goldstandard-Eintrag übertragen können.
`HIVEGENT_RAGOLD__PROJECT` benennt das RAGold-Projekt, dem diese Einträge zugeordnet werden (Standard `Hivegent`).

Skills sind Ordner mit Anweisungen und Skripten im Format [Agent Skills](https://agentskills.io), die der Assistent für passende Aufgaben lädt.
`skills.dir` nennt ein Verzeichnis globaler Skills, je ein Ordner mit einer `SKILL.md`, die alle Benutzer:innen unter `/skills/srv` sehen und niemand über Hivegent ändert, es darf also im Nix-Store liegen.
Benutzer:innen installieren ihre eigenen Skills und die ihrer Gruppen in den Einstellungen als ZIP-Archive, abgelegt in `<data_dir>/skills/`, je höchstens `skills.max_files` Dateien (Standard 512) mit `skills.max_bytes` (Standard 16 MiB).
Skill-Skripte laufen in der Python-Sandbox, daher laufen nur dafür geschriebene Skripte mit einer Funktion `main`, die übrigen bleiben lesbar.

Assistenten sind benannte Anweisungen, die Benutzer:innen im Chat wählen, mit zu ladenden Skills, auszuschaltenden Tools, einem höchsten Modus und Dokumenten im Fokus, ausgeblendet oder als einzige erreichbar, die einen Chat alle nur einschränken.
`assistants.dir` nennt ein Verzeichnis globaler Assistenten, je eine Datei `<name>.json` mit `instructions` und optional `description`, `skills`, `disabled_tools`, `max_mode`, `included_documents`, `excluded_documents` und `allowed_documents`, geprüft beim Start.
Jeder Dokumenteintrag ist ein Objekt wie `{"path": "~/handbuch", "directory": true}`.
Ihre Anweisungen tragen die Autorität Ihrer Konfiguration, während Benutzer:innen ihre eigenen Assistenten und die ihrer Gruppen in `<data_dir>/assistants/` ablegen, geschrieben nur über die Einstellungen und beim Assistenten als zitierter Inhalt ankommend.

Ein Skill oder Assistent einer Gruppe läuft mit den Rechten derer, die ihn nutzen, nie mit denen seiner Autor:innen.
Er erreicht also nichts, was seine Nutzer:innen nicht auch allein erreichen, doch wer die Bibliothek einer Gruppe schreibt, lenkt, was die Läufe ihrer Leser:innen innerhalb deren Rechte tun, etwa welche Dokumente sie lesen oder welche Änderungen sie vorschlagen.
Gewähren Sie Schreibzugriff auf eine Gruppe nur Personen, denen ihre Leser:innen das zutrauen, und prüfen Sie Änderungen an ihren Skills und Assistenten wie Code.

[Plugins](plugins.md) ergänzen Tools, Konverter, Chunker und Skills, aktiviert per Name in `plugins.enabled`.
Der Nix-Build installiert sie aus ihren Projektverzeichnissen in die virtuelle Umgebung des Backends, daher nehmen das Container-Image und das NixOS-Modul sie beim Bauen auf, nicht zur Laufzeit.

`mcp_servers.<id>` verbindet den Assistenten mit einem entfernten MCP-Server, dessen Tools er als `<id>_<tool>` angeboten bekommt.
Die ID besteht aus Kleinbuchstaben, Ziffern und Bindestrichen und darf den Namen eines eingebauten Tools wie `read`, `web` oder `delegate` nicht einleiten, die Delegationstools der Unteragenten eingeschlossen, auch solange diese aus sind, sonst startet der Server nicht.
Einträge folgen der Notation von MCP-Client-Konfigurationen wie `.mcp.json` und Claude Desktop: `type` ist `"http"` (oder `"streamable-http"`) für einen Streamable-HTTP-Endpunkt und `"stdio"` für einen Server, den das Backend als Unterprozess startet, und ohne `type` bedeutet ein `command` stdio und eine `url` HTTP.
Jede Art nimmt nur die Felder ihrer eigenen Art, und `sse` wird abgelehnt.
Die Standardfelder `command`, `args`, `env`, `cwd`, `url` und `headers` behalten Namen und Bedeutung, alles andere hier ist eine Ergänzung.

```toml
[mcp_servers.crm]
url = "https://crm.example.com/mcp"
groups = ["sales"]
blocked_tools = ["delete_account"]
effects = { find_contact = "read" }

[mcp_servers.crm.oauth]
client_id = "hivegent"
issuer = "https://id.example.com"
scopes = ["crm"]
```

- `url`, nur bei HTTP: der Streamable-HTTP-Endpunkt.
- `headers.<name>`: ein fester Header mit einem Klartextwert.
- `secret_headers.<name>`: ein fester Header wie ein API-Schlüssel, übergeben als Secret `hivegent.MCP_SERVERS__<ID>__SECRET_HEADERS__<NAME>`.
- `oauth`: OAuth-Client-Credentials, deren `client_secret` das Secret `hivegent.MCP_SERVERS__<ID>__OAUTH__CLIENT_SECRET` ist und nur an den Autorisierungsserver geht, den `issuer` nennt.
- `groups`: die Gruppen, deren Mitglieder den Server sehen, bei leerer Liste alle und auch die Teams-Bridge.
- `allowed_tools` und `blocked_tools`: die angebotenen Tools, benannt wie auf dem Server, alle, solange `allowed_tools` nicht gesetzt ist.
- `effects`: was Tools dürfen, benannt wie auf dem Server, `read`, `write` oder `execute`, was ihre Hinweise übersteuert.
- `trusted`: ob die Tool-Hinweise des Servers zählen (Standard `false`).
  Ein als nur lesend markiertes Tool liest, eines, das weder als destruktiv noch als offen markiert ist, schreibt, und jedes andere führt aus.
  Jedes andere Tool eines nicht vertrauenswürdigen Servers führt aus.
  Die Anweisungen eines vertrauenswürdigen Servers erreichen auch den Assistenten, mit der Autorität Ihrer eigenen Konfiguration.
- `timeout_seconds`: die Grenze für den Verbindungsaufbau und jede Anfrage (Standard 30).
- `refresh_seconds`: wie oft der Server erneut nach seinen Tools gefragt wird, um eine Änderung zu übernehmen, die er nicht ankündigt (Standard 300).
- `forward_user`, nur bei HTTP: ob jeder Tool-Aufruf dem Server seine Aufrufer:in im Header `X-Hivegent-User` nennt (Standard `false`), eine Person mit dem Claim `sub` ihres Tokens und die Teams-Bridge mit `auth.service_client_id`.
  Sonst wird nichts über die Person gesendet, weder Name noch E-Mail noch Gruppen, und das Abfragen der Tools nennt niemanden.
  Jeder solche Aufruf öffnet eine eigene Verbindung, da der Header zur Aufrufer:in gehört.

Der Claim `sub` ist pseudonym, aber nach der DSGVO dennoch ein personenbezogenes Datum, da der Identitätsanbieter ihn einer Person zuordnet.
Aktivieren Sie `forward_user` nur für einen Server, der Aufrufe zuordnen muss, etwa für seine eigene Zugriffskontrolle oder sein Audit-Log, halten Sie die Übermittlung mit Zweck und Rechtsgrundlage in Ihrem Verzeichnis von Verarbeitungstätigkeiten fest, und schließen Sie einen Auftragsverarbeitungsvertrag, wo eine andere Stelle den Server betreibt.

Ein stdio-Server, etwa ein Community-Paket, wird mit `command` und `args` gestartet, der Befehl muss also im `PATH` des Backends liegen, und `cwd` setzt sein Arbeitsverzeichnis.

```toml
[mcp_servers.git]
command = "uvx"
args = ["mcp-server-git", "--repository", "/mnt/handbook"]
groups = ["sales"]

[mcp_servers.git.env]
LOG_LEVEL = "warning"
```

Das ist die TOML-Form des `mcpServers`-Eintrags `{"git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "/mnt/handbook"], "env": {"LOG_LEVEL": "warning"}}}` samt `groups`.

- `env.<name>`: eine Umgebungsvariable mit einem Klartextwert.
- `secret_env.<name>`: eine Umgebungsvariable wie ein API-Schlüssel, angegeben als Secret `hivegent.MCP_SERVERS__<ID>__SECRET_ENV__<NAME>`.

Der Prozess erhält nur diese Variablen und eine minimale Basis (`PATH`, `HOME` und einige weitere), nie die Umgebung des Backends, die dessen Secrets enthält.
Er wird mit dem Backend gestartet, nach einem Fehler wie jeder andere Server neu gestartet und beendet, wenn das Backend stoppt.
Er läuft mit den Rechten des Backends und außerhalb der Egress-Beschränkung von Hivegent, kann also jede Adresse erreichen, die der Host erlaubt, und nur ein Operator kann einen deklarieren.
Installieren Sie nur Server, denen Sie vertrauen, und setzen Sie `trusted` nur, wenn Sie auch ihren Hinweisen vertrauen.
Mit dem NixOS-Modul legt `services.hivegent.mcpPackages` die Pakete, die den Befehl bereitstellen, in den `PATH` des Dienstes, etwa `[ pkgs.uv ]`, und ein Server, der das Netz braucht, wozu auch `uvx` und `npx` beim Laden eines Pakets gehören, braucht `allowedAddresses` für seine Adressen.

Ein lesendes Tool läuft in jedem Modus, jedes andere wird im Modus Lesen nicht angeboten und fragt vor jedem Aufruf nach, sodass der Teams-Bridge nur die lesenden Tools von Servern ohne `groups` bleiben.
Jeder Aufruf handelt mit den Zugangsdaten des Deployments statt denen der Person, geben Sie einem Server daher nur, was jedes Mitglied seiner `groups` tun darf.
Hivegent hält eine Verbindung pro Server (bei stdio einen Prozess) und verbindet sich mit wachsendem Abstand neu, und solange ein Server nicht erreichbar ist, werden seine Tools mit einem kurzen Hinweis an den Assistenten zurückgehalten, statt die Antwort scheitern zu lassen.
Admins sehen den Status jedes Servers und die Wirkung seiner Tools unter Administration, Integrationen, und Nutzer schalten die Tools wie jedes andere unter Einstellungen, Tools ab.

`databases.<id>` lässt den Assistenten eine SQL-Datenbank erkunden und lesen sowie die Statements ausführen, die der Betreiber geschrieben hat, und `apis.<id>` die Operationen einer HTTP-API aufrufen, als Tools namens `<id>_<tool>` mit denselben ID-Regeln wie bei einem MCP-Server, die gemeinsam gelten, sodass keine ID zwei davon benennt.

```toml
[databases.erp]
url = "postgresql+psycopg://hivegent@db.example.com:5432/erp"
tables = ["orders", "customers"]
groups = ["sales"]
instructions = "Bestellungen der letzten fünf Jahre, Beträge in Euro."

[databases.erp.statements.close_order]
description = "Schließt eine Bestellung anhand ihrer ID."
sql = "UPDATE orders SET closed = true WHERE id = :id"
params = { id = "integer" }
effect = "write"

[apis.crm]
url = "https://crm.example.com/api/v1"
spec = "/etc/hivegent/crm-openapi.yaml"
operations = ["getContact", "addNote"]
headers = {}
```

- `databases.<id>.url`: eine SQLAlchemy-URL ohne Passwort, das das Secret `hivegent.DATABASES__<ID>__PASSWORD` ist, und der Treiber ihres Dialekts muss installiert sein, was beim Start geprüft wird.
  PostgreSQL, MySQL und SQLite werden unterstützt.
- `tables`: die Tabellen und Views, die der Assistent auflistet, beschreibt und über `<id>_select` liest, und keine anderen.
  Der Assistent schreibt nie SQL, eine Auswahl nennt Tabelle, Spalten, Filter, Gruppen, Aggregate und Sortierung, die gegen die Tabelle geprüft und mit gebundenen Werten übersetzt werden.
  Ein Join ist eine View, die der Betreiber anlegt und hier nennt.
- `statements.<name>`: ein Statement des Betreibers, angeboten als Tool `<id>_<name>` mit seiner `description`, dessen `sql` jeden Parameter als `:name` bindet und dessen `params` ihre Typen angeben, `string`, `integer`, `number`, `boolean`, `date` oder `datetime`.
  Ein Statement, dessen Parameter von denen abweichen, die sein `sql` bindet, verhindert den Start des Servers.
  `effect = "read"` (Standard) führt es in einer schreibgeschützten Transaktion aus, `effect = "write"` schreibt es fest und fragt vor jedem Aufruf nach.
  Eine Datenbank braucht `tables`, `statements` oder beides.
- `max_rows` und `timeout_seconds`: die Zeilen, die eine Auswahl oder ein Statement liefert (Standard 100), und ihr Statement-Timeout (Standard 20).
  Die Rechte des Datenbankkontos begrenzen weiterhin, was ein Statement berührt, geben Sie dem Konto daher nur, was Tabellen und Statements brauchen.
- `apis.<id>.url` und `spec`: die Basis-URL, an die Anfragen gehen und deren Origin die einzige ist, die sie erreichen dürfen, und der Pfad des OpenAPI-3-Dokuments, JSON oder YAML, dessen `$ref`s auf die eigenen `components` aufgelöst werden.
- `operations`: die angebotenen `operationId`s, jede als Tool `<id>_<operationId>` mit den Parametern der Operation, und eine unbekannte ID verhindert den Start des Servers.
  Header- und Cookie-Parameter werden nicht angeboten, das Deployment authentifiziert stattdessen.
- `headers.<name>`: ein fester Header mit einem Klartextwert, etwa `OCS-APIRequest = "true"` für die OCS-API von Nextcloud.
- `secret_headers.<name>`: ein fester Header wie ein API-Schlüssel, übergeben als Secret `hivegent.APIS__<ID>__SECRET_HEADERS__<NAME>`.
  Ein Name ist entweder Klartext oder Secret, nicht beides.
- `max_response_bytes` und `timeout_seconds`: die Bytes einer Antwort, die gelesen werden (Standard 1 MB), und die Grenze für eine Anfrage (Standard 20).
- `groups` und `instructions`: die Gruppen, deren Mitglieder sie sehen, bei leerer Liste alle und auch die Teams-Bridge, und was der Assistent über ihre Daten erfährt oder wann er sie nutzen soll.

Eine Auswahl, ein lesendes Statement und eine Operation per `GET` oder `HEAD` lesen und laufen in jedem Modus, ein schreibendes Statement und jede andere Methode werden im Modus Lesen nicht angeboten und fragen vor jedem Aufruf nach, sodass der Teams-Bridge nur die lesenden Tools von Quellen ohne `groups` bleiben.
Wie die Tools eines MCP-Servers gehören sie weder zu einer Person noch zu den Arbeitsbereichen, ein Assistent, der die Dokumente eingrenzt, bietet sie also weiterhin an.
Jeder Aufruf handelt mit den Zugangsdaten des Deployments statt denen der Person, geben Sie einem Konto oder API-Schlüssel daher nur, was jedes Mitglied der `groups` tun darf.
Admins sehen Host, Gruppen und die Wirkung der Tools jeder Quelle unter Administration, Integrationen, und Nutzer schalten die Tools wie jedes andere unter Einstellungen, Tools ab.

`sources.<id>` spiegelt eine externe Dateiablage, etwa einen Nextcloud-Ordner, in Hivegent, wo die Mitglieder ihrer `groups` sie unter `/mnt/<id>` finden und wie jeden Arbeitsbereich durchsuchen, zitieren und ansehen.
Nur diese Konfiguration legt Quellen an, ihre Geheimnisse in Credential-Dateien, sodass weder Nutzer noch Gruppen eine hinzufügen oder ihre Zugangsdaten erreichen.

```toml
[sources.handbook]
connector = "webdav"
url = "https://cloud.example.com/remote.php/dav/files/hivegent/Handbook/"
username = "hivegent"
groups = ["sales"]
interval_minutes = 15
```

- `connector`: wie die Quelle erreicht wird, `webdav` eingebaut für WebDAV-Server wie Nextcloud und `local` für ein eingehängtes Verzeichnis, weitere aus aktivierten [Plugins](plugins.md).
- `url`: die Wurzel der Quelle, von `webdav` benötigt, deren Host, oder einer aus `hosts`, der einzige ist, den eine Anfrage erreichen darf.
- `username` und `password`: Basic-Authentifizierung, das Passwort als Geheimnis `hivegent.SOURCES__<ID>__PASSWORD`.
  Für Nextcloud teilen Sie den Ordner mit einem eigenen Konto, legen dafür unter Einstellungen, Sicherheit ein App-Passwort an und lassen `url` auf den Ordner unter `/remote.php/dav/files/<konto>/` zeigen.
- `groups`: die Gruppen, deren Mitglieder die Quelle sehen, alle und die Teams-Bridge, wenn leer.
- `interval_minutes`: wie oft die Quelle abgeglichen wird (Standard 15).
  Eine Datei, deren Löschung die Quelle meldet, wie Nextcloud es tut, oder die in einer vollständigen Auflistung fehlt, wird beim nächsten Abgleich gelöscht, wie bei rclone, da der Spiegel ein Cache ist und eine falsche Löschung nur einen neuen Download kostet.
  Eine Auflistung, die die Quelle teilweise verweigerte, löscht nur die Dateien, deren Löschung die Quelle meldet.
- `max_sweep_share`: der Anteil des Spiegels, den ein Abgleich für Dateien löschen darf, die die Quelle verloren hat, weil sie in einer Auflistung fehlen oder nicht mehr gespiegelt werden können (Standard 0,5), darüber, und über 10 Dateien, oder bei einer leeren Auflistung warten diese Löschungen auf die Bestätigung eines Admins unter Administration, Quellen, da ein Ausfall wie ein leerer Ordner aussieht.
  Zurückgehaltene Dateien bleiben im Spiegel, bis die Quelle sie wieder auflistet oder ein Admin bestätigt, von der Quelle gemeldete Löschungen werden nie zurückgehalten, und der Rest des Abgleichs wird übernommen.
- `options`: die eigenen Einstellungen des Connectors, für `webdav` `max_depth`, die tiefste Ordnerebene unter `url`, die gelistet wird (Standard 32), da eine tiefere den Abgleich scheitern lässt, und `max_file_size_bytes`, die größte Datei, die gelesen wird (Standard 50 MiB).
- `conversion` und `chunking`: die IDs der Pipelines, mit denen ihre Dateien konvertiert und in Chunks zerlegt werden (Standard `auto`, wie bei einem Upload), beim Start geprüft.

Eine Quelle ist schreibgeschützt: weder der Assistent noch jemand anderes ändert sie oder ihren Spiegel, den nur der Abgleich schreibt.
Ein Abgleich lädt nur, was sich geändert hat, Nextcloud meldet das günstig und jeder andere WebDAV-Server wird vollständig aufgelistet, und er konvertiert und indexiert die Dateien wie Uploads nach `sources/<id>` im Datenverzeichnis.
Eine umbenannte Datei behält ihr Dokument, während versteckte Dateien und eine Datei, die ihren Namensstamm mit einer anderen teilt, etwa `report.pdf` neben `report.docx`, übersprungen werden.
Ein Ordner oder eine Datei, deren Auflistung der Server verweigert, etwa weil das Konto sie nicht lesen darf, lässt den Abgleich nicht scheitern, der wie bei rclone alles andere spiegelt, aber keine in dieser Auflistung fehlende Datei löscht und die verweigerten Pfade als Fehler der Quelle nennt, während eine Wurzel, die der Server verweigert oder nicht beantwortet, den Abgleich scheitern lässt.
Admins sehen unter Administration, Quellen den letzten Abgleich, die Anzahl der Dateien, den Fehler, den Fortschritt und die zurückgehaltenen Löschungen jeder Quelle und fordern dort einen Abgleich an, den ein Serverprozess innerhalb einer Minute startet, oder gleich nach dem noch laufenden Abgleich.
Ein Abgleich, der Löschungen zurückhält, lädt und verschiebt trotzdem alles andere, und ein Admin bestätigt die Löschungen dort, nachdem er die Quelle geprüft hat.
Eine aus der Konfiguration entfernte Quelle verliert beim nächsten Start ihren Spiegel und ihre Dokumente.
Die Quelle ist ein Endpunkt des Betriebs, der direkt erreicht wird, auch in einem privaten Netz, sodass ein gesetztes `allowedAddresses` sie abdecken muss.

Eine `local`-Quelle spiegelt ein Verzeichnis, das der Betrieb in den Server eingehängt hat, ohne eigene `url`, `hosts` oder Zugangsdaten, da das Betriebssystem den Zugriff übernimmt.
Sie listet bei jedem Abgleich alle Dateien auf, nutzen Sie für Nextcloud daher stattdessen `webdav`, das Änderungen und Umbenennungen günstig meldet.

```toml
[sources.share]
connector = "local"
options = { path = "/mnt/share" }
```

- `path`: das absolute Verzeichnis, das ein aktiver Einhängepunkt sein muss, außer bei `require_mount = false`, damit eine getrennte Freigabe den Abgleich scheitern lässt, statt wie eine leere auszusehen.
- `max_depth` und `max_file_size_bytes`: wie bei `webdav`.

Symlinks und Spezialdateien werden übersprungen und nie verfolgt, auch nicht, wenn eine Datei nach der Auflistung durch einen ersetzt wird, und ein Ordner oder eine Datei, die nicht gelesen werden kann, wird wie bei `webdav` als verweigert genannt.
Eine SMB-Freigabe wird unter NixOS mit einer Zugangsdatei mit den Zeilen `username=` und `password=` eingehängt, die nur root lesen darf:

```nix
fileSystems."/mnt/share" = {
  device = "//files.example.com/share";
  fsType = "cifs";
  options = [ "credentials=/run/secrets/share" "ro" "uid=hivegent" "x-systemd.automount" "noauto" ];
};
```

Mit Docker hängt ein benanntes Volume sie ein, und der Dienst bindet es schreibgeschützt unter demselben Pfad:

```yaml
services:
  hivegent:
    volumes:
      - share:/mnt/share:ro
volumes:
  share:
    driver_opts:
      type: cifs
      device: //files.example.com/share
      o: username=hivegent,password=secret,ro
```

Die Seiten der Administration zeigen die geltenden Modelle, Plugins, Integrationen, Quellen, Pipelines und globalen Skills schreibgeschützt, da sie alle aus dieser Konfiguration stammen, und enthalten den Wartungsmodus und die Neuindexierung.
Ihre Übersicht listet die Benutzer und Gruppen, die etwas gespeichert haben, mit ihren Dokumenten und Unterhaltungen, da die Mitgliedschaft allein beim Identitätsanbieter liegt.
Admins können nicht als andere Benutzer handeln, das Annehmen fremder Identitäten wurde bewusst entfernt, sodass ein Admin nur sieht, was sein eigenes Token gewährt.

`HIVEGENT_DEBUG__ENABLE=true` ergänzt den Tool- und Chat-Debugger für Admins, die es sonst nicht gibt.
Der Chat-Debugger öffnet nur die eigenen Unterhaltungen des Admins.

Traces werden über OTLP/HTTP an `logfire.otlp_endpoint` exportiert, etwa Grafana Tempo, und mit dem Credential `hivegent.LOGFIRE__TOKEN` an Pydantic Logfire.
Ihre Anfrage-Spans enthalten keine Argumente der API, etwa Chat-Inhalte, und ihre Modell- und Tool-Spans keine Prompts, Antworten oder Tool-Argumente und -Ergebnisse, außer mit `logfire.include_content = true`, das auch MCP-Anfragen und Sandbox-Code mit ihren Inhalten aufzeichnet, und nie Bilder oder andere Binärinhalte.

`rate_limits.<route>` begrenzt Chat-Züge, Antworten auf Freigaben und Fragen eines Zugs (`resume`), Uploads, Transkriptionen, Generierungen und Wasserzeichenprüfungen jeder Person als Token-Bucket aus `burst` Anfragen, der mit `per_minute` nachgefüllt wird.
Jeder Serverprozess hält eigene Buckets, hinter einem Load Balancer mit mehreren Servern erhält eine Person jede Grenze also einmal pro Server.
`jobs.max_owner_jobs` (Standard 20) und `jobs.max_owner_spool_bytes` (Standard 2 GiB) begrenzen die unfertigen Upload-Jobs jeder Person und die Bytes, die diese zwischengespeichert auf der Platte halten, wer eine überschreitet, erhält ein 429.
`rate_limits.teams` enthält dieselben Grenzen für die Teams-Bridge, deren Buckets sich alle Teams-Benutzer teilen, weshalb `request` und `chat` standardmäßig weit höher liegen, 200 Chat-Züge auf einmal und 120 pro Minute.

Das `/tmp` einer Unterhaltung wird nach `conversations.ttl_hours` ohne Aktivität gelöscht (Standard 72), einstellbar mit `HIVEGENT_CONVERSATIONS__TTL_HOURS`.
Zur Freigabe vorgemerkte Änderungen bleiben, bis sie beantwortet sind oder die Unterhaltung gelöscht wird.

Jede vom Modell gesteuerte Web-Anfrage läuft durch den mitgelieferten Smokescreen-Proxy, der private und reservierte Adressen ablehnt.
Vom Betreiber konfigurierte URLs wie die `base_url` eines Anbieters verbinden sich direkt, und keine Anfrage kann einen anderen Endpunkt nennen.
Die NixOS-Option `services.hivegent.allowedAddresses` beschränkt das Backend zusätzlich auf Loopback und die aufgeführten Adressen, daher müssen diese dann lokal, ein Unix-Socket oder aufgeführt sein, während Modell-Downloads durch den Proxy laufen.

Die Websuche braucht einen Metasuchdienst, den Hivegent nicht mitliefert, so wie es auch PostgreSQL Ihnen überlässt.
SearXNG ist das voreingestellte Backend: `compose.yaml` startet dessen offizielles Image, und die NixOS-Option `services.hivegent.searxng.createLocally` bindet das `services.searx` des Hosts ein.
OpenSERP funktioniert ebenfalls mit `tools.search_backend = "openserp"`, durchsucht allerdings nur das allgemeine Web und meldet kein Veröffentlichungsdatum.
Setzen Sie `tools.search_url` auf den Dienst, sonst bleibt `web_search` verborgen, und tragen Sie bei SearXNG `json` unter `search.formats` ein.
`web_fetch` wird getrennt über `[security.web_urls] allow_hosts` aktiviert, wobei `"*"` jede öffentliche Seite erlaubt, die der Benutzer oder eine Suche genannt hat, und eine Liste von Domains den Abruf auf diese beschränkt.
`web_fetch` lehnt eine URL ab, die das Modell selbst schreibt, damit auf einer Seite platzierte Anweisungen keine Gesprächsdaten in einer Anfrage hinausschleusen können.
Es behält keine Cookies, sodass keine Seite eine Sitzung sieht, die eine Anfrage einer anderen Unterhaltung begonnen hat.
Der Chat lädt nie ein Bild aus der Antwort des Modells und fragt nach, bevor er einen darin enthaltenen Link öffnet, sodass beides beim Anzeigen keine Daten preisgibt.

Um einen Betreiber-Endpunkt über einen Unix-Socket zu erreichen, ordnen Sie seinen Hostnamen unter `[network.unix_sockets]` zu, etwa mit `HIVEGENT_NETWORK__UNIX_SOCKETS='{"llmhop": "/run/llmhop/default.sock"}'`.
Ein Anbieter mit `base_url = "http://llmhop/v1"` verbindet sich dann mit dem Socket.
Web-Anfragen nutzen diese Zuordnung nie.
Ein direkter Client erreicht nur die Origins der für ihn konfigurierten Endpunkte und lehnt eine Weiterleitung oder einen Pfad zu jedem anderen Host ab.
Das NixOS-Modul läuft als dynamischer Benutzer, geben Sie dem Socket daher eine eigene statische Gruppe (nicht `hivegent`) mit Modus `0660` und fügen Sie sie zu `systemd.services.hivegent.serviceConfig.SupplementaryGroups` hinzu.

## Identitätsanbieter (OIDC)

Hivegent funktioniert mit jedem OIDC-Anbieter, etwa Rauthy, Keycloak, Authentik oder Auth0.
Der Browser liest Aussteller und Client-ID zur Laufzeit vom Backend, ein Neubau ist nicht nötig.
Die Content-Security-Policy des mitgelieferten Caddy lässt die App keinen anderen Host als die Origins des Anbieters erreichen, die Sie ausdrücklich nennen, meist den Origin des Ausstellers und jeden weiteren Origin, auf dem der Anbieter anmeldet.
Der Container nimmt sie durch Leerzeichen getrennt in `CADDY_IDP_ORIGINS`, das NixOS-Modul als Liste `services.hivegent.caddy.idpOrigins`.
Ohne Origin wäre die Anmeldung blockiert, daher startet der Container nicht und das NixOS-Modul lässt sich nicht auswerten.

Das Backend nimmt ein Access-Token nur an, wenn es mit einem asymmetrischen Algorithmus signiert ist, den der Anbieter ankündigt (RS, PS, ES, EdDSA oder Ed25519), den konfigurierten Issuer und eine Audience aus `auth.audience` nennt, `exp` und `iat` enthält, seinen Client in `azp` oder `client_id` nennt und ein Access-Token ist, ausgewiesen durch den Header `typ: at+jwt` nach RFC 9068 oder den Claim `typ: Bearer` von Keycloak und Rauthy.
Ein ID-Token desselben Clients wird abgelehnt, daher muss ein Anbieter, der seine Access-Tokens auf keine der beiden Arten kennzeichnet, auf Tokens nach RFC 9068 umgestellt werden.
Das Discovery-Dokument des Anbieters muss denselben Issuer nennen und seine Schlüssel auf dem Origin des Issuers bereitstellen, Schema eingeschlossen.
Der MCP-Endpunkt prüft Tokens genauso, für die Audience `mcp.base_url`, genau so geschrieben wie konfiguriert, oder `mcp.client_id`.
Der mitgelieferte Caddy leitet `/mcp` und die OAuth-Discovery-Dokumente des Endpunkts unter `/.well-known/` weiter, etwa `/.well-known/oauth-protected-resource/mcp`, und im Modus `proxy` muss der Identitätsanbieter die Redirect-URI `<mcp.base_url>/auth/callback` erlauben.
Das Backend-Log nennt, warum es ein Token abgelehnt hat.

Für das mitgelieferte Rauthy:

1. Lesen Sie das einmalige Administrator-Passwort mit `docker compose logs rauthy`.
2. Melden Sie sich unter <http://auth.localhost:8081> an.
3. Legen Sie einen öffentlichen Client `hivegent-spa` mit PKCE (S256) an und setzen Sie Redirect-URI und erlaubten Ursprung auf `http://localhost:8080`.
4. Legen Sie Benutzer und optional Gruppen an.

### Gruppen und Rollen

Der Zugriff richtet sich nach den Token-Claims, die Ihr Anbieter in das Access-Token aufnehmen muss.

- Jeder Benutzer hat einen privaten Arbeitsbereich und liest den globalen (`/srv`), den nur Benutzer mit der Rolle `admin` schreiben.
- Gruppen stammen aus dem Claim `groups` oder den Claim-Pfaden, die `claims.groups` auflistet, etwa `[["groups"], ["custom", "groups"]]` für die statischen Claims, die ein Rauthy-Client unter `custom` verschachtelt.
  Ein Eintrag wie `engineering` gewährt den geteilten Arbeitsbereich, `engineering:read` oder `engineering:write` legt die Berechtigung fest.
- Die Rolle `admin` aus dem Claim `roles` oder den Pfaden, die `claims.roles` auflistet, gewährt Administratoraktionen wie Wartungsmodus und Neuindizierung.
  Keine HTTP-Route löscht die Daten einer anderen Person oder einer Gruppe, das geschieht nur auf dem Host.
  Löschungen des gesamten Deployments laufen auf dem Host mit `hivegent reset workspace`, `database` oder `factory` und verweigern sich, solange ein Server auf der Datenbank läuft.
  `hivegent reset user <id>` und `hivegent reset group <id>` löschen, was einer Person oder Gruppe gehört, während Benutzer ihre eigenen Daten unter Einstellungen, Account löschen.
  `hivegent-manage` führt jeden `hivegent`-Befehl genau so aus, wie der Dienst läuft, mit seiner Umgebung, seinen Zugangsdaten und Daten, und stoppt den Server für ein `reset` und startet ihn danach wieder.
  Im Container läuft es im laufenden Container:

  ```sh
  docker compose exec hivegent hivegent-manage reset group engineering
  ```

  Unter NixOS läuft es als root mit dem dynamischen Benutzer, den Verzeichnissen und der Sandbox des Dienstes, über `systemd-run --pty --wait --collect`:

  ```sh
  sudo hivegent-manage reset group engineering
  ```

  `reset` verweigert sich, solange der Server auf derselben Datenbank läuft, da er dort eine Sperre hält, stoppen Sie ihn also zuerst.
  Pro Datenbank und Datenverzeichnis läuft ein Server, ein zweiter verweigert den Start.

  Eine aus der Konfiguration entfernte Quelle wird beim nächsten Start mit ihrem Spiegel gelöscht, außer sie hält Dateien, die ihre Quelle verloren hat und deren einzige Kopie der Spiegel ist.
  Eine solche Quelle bleibt, für alle Nutzer verborgen, erhalten und wird protokolliert, bis sie wieder konfiguriert oder mit `hivegent reset sources` gelöscht wird.

### Gruppenidentität und Umbenennen

Ein Eintrag im Gruppen-Claim ist entweder ein Objekt oder eine reine Zeichenkette.

Ein Objekt nach RFC 9068, `{"value": "<id>", "display": "<name>"}`, trennt Kennung und Anzeigename.
Die Kennung erscheint in Pfaden (`@<id>/notes.md`), daher bleibt der Arbeitsbereich beim Umbenennen erhalten.
So arbeiten Entra ID sowie Keycloak, Authentik, Okta oder Auth0 mit einem Claim-Mapper auf die Gruppen-ID.

Eine reine Zeichenkette, mehr kann Rauthy nicht ausgeben, ist Name und Kennung zugleich.
Beim Umbenennen einer solchen Gruppe bleiben ihre Dokumente unter dem alten Namen zurück.

## Skripte

Das Kommandozeilenwerkzeug `hivegent` administriert nur den Server auf seinem Host, seine früheren Client-Befehle `login`, `logout`, `upload`, `download`, `list`, `delete` und `whoami` wurden bewusst entfernt.
Skripte nutzen stattdessen die REST-API oder den MCP-Endpunkt, mit einem Zugriffstoken Ihres Identitätsanbieters, dessen Client in `auth.audience` steht.
Das Token einer Person aus dem Device Flow handelt als diese Person, eines aus dem Client Credentials Flow als eigener Benutzer seines Clients, dessen `sub` seinen privaten Arbeitsbereich benennt.
Der Client von `auth.service_client_id` erreicht nur die Chat-Route, nutzen Sie ihn daher nicht für Skripte.

```sh
ISSUER=https://auth.example.com/auth/v1
API=https://hivegent.example.com/api/v1
TOKEN_URL=$(xh $ISSUER/.well-known/openid-configuration | jq -r .token_endpoint)
TOKEN=$(xh --form POST $TOKEN_URL grant_type=client_credentials \
  client_id=hivegent-script client_secret=$CLIENT_SECRET | jq -r .access_token)

xh -A bearer -a $TOKEN GET "$API/directories/~"                                 # auflisten
xh -A bearer -a $TOKEN --form PUT "$API/documents/~/reports/q3.pdf" file@q3.pdf # hochladen
xh -A bearer -a $TOKEN GET "$API/documents/original/~/reports/q3.pdf" -o q3.pdf # herunterladen
xh -A bearer -a $TOKEN DELETE "$API/documents/~/reports/q3.pdf"                 # löschen
```

Pfade beginnen mit `~` für den privaten Arbeitsbereich, `@<gruppe>` für den einer Gruppe und `/srv` für den globalen.
Ein Upload antwortet mit einem Job, dem `GET /api/v1/jobs` folgt, bis das Dokument konvertiert und indiziert ist.
`POST /api/v1/changes` verschiebt, löscht und erstellt mehrere Dokumente und Verzeichnisse auf einmal, alle oder keine.
Mit `security.expose_api_docs` steht das Schema jeder Route unter `/api/v1/docs` bereit.

Ein MCP-Client wie die Python-Pakete `mcp` oder `fastmcp` verbindet sich mit derselben Art Token mit `mcp.base_url`, das ohne abschließenden Schrägstrich auf `/mcp` endet, und ruft die Tools `list_documents`, `read_document`, `write_document`, `move_documents` und `delete_documents` auf, wobei jede Änderung zuerst um Bestätigung bittet.

## Aktualisieren

```bash
docker compose pull
docker compose up -d
```

Datenbankmigrationen laufen beim Start automatisch.
Der Index folgt Änderungen des Embedding-Modells, der Volltextkonfigurationen und der Chunking-Optionen beim nächsten Start von selbst, ebenso einem Update, das Dokumente anders zerlegt oder einbettet.
Lesen Sie vor einem Update die [Hinweise zum Upgrade](upgrading.md), da entfernte Einstellungen den Start des Servers verhindern.

## Mehrere Instanzen

Instanzen wie Produktion, Staging und Test sind unabhängig, wenn jede eine eigene Datenbank, ein eigenes Datenverzeichnis und einen eigenen OIDC-Client hat, da das Backend die Audiences von Tokens exakt prüft.
Pro Datenbank und Datenverzeichnis läuft ein Server, ein zweiter verweigert den Start.

Unter NixOS betreibt `nixosModules.instances` jede Instanz in einem Container mit dem unveränderten Hivegent-Modul, einer eigenen PostgreSQL und einem privaten Netz, während Caddy auf dem Host jede Domain mit TLS ausliefert:

```nix
{
  imports = [ hivegent.nixosModules.instances ];

  services.hivegent.instances.staging = {
    hostName = "staging.hivegent.example.com";
    hostAddress = "10.233.2.1";
    address = "10.233.2.2";
    config.services.hivegent = {
      caddy.idpOrigins = [ "https://auth.example.com" ];
      settings = { /* wie bei einer einzelnen Instanz */ };
    };
    # Zugangsdaten-Dateien des Hosts, im Container sichtbar.
    container.bindMounts."/run/secrets/hivegent".hostPath = "/run/secrets/hivegent-staging";
  };
}
```

Namen haben höchstens 11 Zeichen, die Namen der Container.
Host-Operationen laufen im Container, etwa `sudo nixos-container run staging -- hivegent-manage reset sources`.
Andernorts läuft pro Instanz ein eigener Container-Stack mit eigener PostgreSQL und eigenem Volume.

## Hinweise für den Produktivbetrieb

- Ersetzen Sie die Beispiel-Geheimnisse, Schlüssel und Passwörter.
- Betreiben Sie den Stapel über HTTPS, mit einer echten Domain für den mitgelieferten Proxy oder einem eigenen Reverse-Proxy.
- Ein laufender Server hält einen PostgreSQL-Advisory-Lock auf Sitzungsebene, den `hivegent reset` prüft, daher muss ein PgBouncer vor der Datenbank Session Pooling nutzen, nicht Transaction oder Statement Pooling.
- Ein Server, der die Verbindung seiner Sperre verliert, beendet sich, und sein Dienst startet ihn neu, die NixOS-Unit mit einem Abstand von 5 bis 60 Sekunden und der Container mit mindestens 10 Sekunden Abstand, während ein Stopp durch den Betrieb gestoppt bleibt.
