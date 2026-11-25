# Hinweise zum Upgrade

Lesen Sie diese Hinweise, bevor Sie ein bestehendes Deployment aktualisieren.
Jeder Schlüssel der `config.toml` und jede Variable `HIVEGENT_*` muss eine Einstellung benennen, daher verhindert ein entfernter oder falsch geschriebener Schlüssel den Start des Servers mit einer Fehlermeldung, die ihn nennt, statt still ignoriert zu werden.

## Entfernte und umbenannte Einstellungen

| Vorher                                                                                                                                      | Jetzt                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `llm.model`                                                                                                                                 | `llm.models.<id>.model`, gewählt durch `llm.default`                                                                 |
| `llm.base_url`, `llm.api_key`, `llm.inference_provider`                                                                                     | `llm.providers.<id>.base_url`, `api_key` und `inference_provider`, benannt durch `llm.models.<id>.provider`          |
| `llm.max_tokens`                                                                                                                            | `llm.models.<id>.max_tokens`                                                                                         |
| `llm.aux_model`                                                                                                                             | `llm.aux`, die ID eines Modells des Katalogs                                                                         |
| `llm.stt_model`                                                                                                                             | `llm.stt`, die ID eines Modells des Katalogs                                                                         |
| `llm.subagent_timeout_seconds`                                                                                                              | `subagents.timeout_seconds`                                                                                          |
| `embedding.base_url`, `embedding.api_key`                                                                                                   | `embedding.endpoint`, die ID eines Eintrags von `llm.providers`                                                      |
| `rerank.base_url`, `rerank.api_key`                                                                                                         | `rerank.endpoint`, die ID eines Eintrags von `llm.providers`                                                         |
| `transparency.detector_url`, `transparency.detector_api_key`                                                                                | `transparency.endpoint`, die ID eines Eintrags von `llm.providers`, und `transparency.detector_path`                 |
| `auth.enable`, `auth.allow_disabled`                                                                                                        | entfernt, `hivegent serve` authentifiziert immer und braucht `auth.issuer`, `hivegent dev` nie                       |
| `mcp.enable`                                                                                                                                | entfernt, `mcp.base_url` zu setzen stellt `/mcp` bereit                                                              |
| `mcp.allow_unauthenticated`                                                                                                                 | entfernt, `/mcp` authentifiziert unter `hivegent serve` immer                                                        |
| `security.user_urls`                                                                                                                        | entfernt, Benutzer:innen bringen keine eigenen Modell- oder MCP-Endpunkte mehr mit, nur der Betrieb konfiguriert sie |
| `LOGFIRE_TOKEN`                                                                                                                             | `logfire.token`, am besten als Credential `hivegent.LOGFIRE__TOKEN`, da die Variable nicht mehr gelesen wird         |
| Voreinstellung der drei Gesprächswerkzeuge in `tools.disabled`                                                                              | `[]`, da ihr Zurückhalten nun auch den Unteragenten `conversations` abschaltet                                       |
| `HIVEGENT_SITE_ADDRESS` (Container)                                                                                                         | `CADDY_SITE_ADDRESS`                                                                                                 |
| `enableMcp` (`docker.override`)                                                                                                             | entfernt, Caddy leitet `/mcp` immer weiter, das das Backend nur mit gesetztem `mcp.base_url` bereitstellt            |
| Voreinstellung `custom.*` von `claims.groups`, `claims.roles`                                                                               | `[["groups"]]` und `[["roles"]]`, Pfade sind Listen von Schlüsseln wie `["custom", "groups"]`                        |
| Rückfall von `network.contact_email` auf den Paketautor                                                                                     | keiner, ohne Adresse nennt der `User-Agent` keinen Kontakt                                                           |
| `hivegent.LLM__API_KEY`, `hivegent.EMBEDDING__API_KEY`, `hivegent.RERANK__API_KEY`, `hivegent.TRANSPARENCY__DETECTOR_API_KEY` (Credentials) | `hivegent.LLM__PROVIDERS__<ID>__API_KEY` des Anbieters, den sie nutzen                                               |

Das neue `CADDY_IDP_ORIGINS` des Containers, durch Leerzeichen getrennt, und `services.hivegent.caddy.idpOrigins` des NixOS-Moduls, eine Liste, nennen die Origins des Identitätsanbieters für die Content-Security-Policy der App.
Sie sind Pflicht, ohne Origin startet der Container nicht und das NixOS-Modul lässt sich nicht auswerten, da die Policy sonst die Anmeldung blockieren würde.
Ein `HIVEGENT_CONFIG_FILE`, das keine Datei nennt, verhindert nun den Start, und der Container nennt `/data/config.toml`, binden Sie also eine ein.
`docsPath` muss mit einem Schrägstrich beginnen und ohne einen enden, ein abschließender Schrägstrich wird abgelehnt statt entfernt.
Einstellungen, die eine leere Zeichenkette als ungesetzt nahmen, bleiben nun stattdessen ungesetzt: `auth.issuer`, `mcp.base_url`, `mcp.client_id`, `network.contact_email` und `transparency.contact_email`.
Ein leeres `auth.issuer`, `mcp.base_url`, `mcp.client_id`, `network.contact_email` oder `transparency.contact_email` verhindert den Start, ebenso eine Betreiber-URL, die keine ist, etwa ein `auth.issuer` ohne Schema.
Ein Qwen3.8-Modell sendet für die Denkstufe `high` nicht mehr anhand seines Namens `xhigh`, setzen Sie stattdessen `reasoning_overrides = { high = "xhigh" }` an ihm.
Exportierte Traces enthalten keine Nachrichteninhalte mehr, setzen Sie `logfire.include_content = true`, um Prompts, Antworten sowie Tool-Argumente und -Ergebnisse darin zu behalten, während Bilder und andere Binärinhalte gar nicht mehr exportiert werden.
Die Anfrage-Spans der API enthalten ihre Argumente, etwa Chat-Inhalte, nur noch damit und behalten von einem Validierungsfehler nur Art und Ort.
Die Spans von MCP und der Monty-Sandbox werden nur noch damit aufgezeichnet, da sie ganze Nutzdaten und den ausgeführten Code enthalten.
`mcp.base_url` und `transparency.report_issuer` müssen URLs sein, `llm.default`, `llm.aux` und `llm.stt` Katalog-IDs und `logging.level` eines von `DEBUG`, `INFO`, `WARNING`, `ERROR` oder `CRITICAL` in Großbuchstaben.

## Embedding und Reranking

`embedding` und `rerank` nehmen jeweils nur die Felder ihres `provider` an, und der Start lehnt die übrigen ab.

- `embedding.text_search_config` ist eine Liste, eine einzelne Konfiguration wie `"simple"` wird also zu `["simple"]`.
- Ein entfernter Embedder (`provider = "openai"`) braucht `endpoint`, `model` und `dimension`, während der lokale seine Standardwerte behält.
- Reranking ist aus, solange der Abschnitt `rerank` fehlt, und ein Abschnitt `rerank` braucht `provider` und `model`.
  `rerank.top_n` behält die besten neu bewerteten Kandidaten beider Anbieter.
- Ein entfernter Embedder kürzt zu lange Texte auf die Grenze, die das Modell meldet, was nur OpenAIs eigene Modelle tun.
  Setzen Sie `embedding.request.extra_body.truncate_prompt_tokens = -1` für vLLM, da ein anderer Server sie selbst kürzen muss.
  Er sendet nun `embedding.batch_size` Texte je Anfrage (Standard 128) und `embedding.concurrency` Anfragen gleichzeitig (Standard 4).
- `embedding.query_prefix` und `embedding.document_prefix` stellen jeder Anfrage und jedem Chunk ein Präfix oder eine Anweisung voran, die Modelle wie e5 und Qwen3-Embedding erwarten.
  Eine Änderung von `embedding.document_prefix` bettet wie ein neues Modell beim nächsten Start alle Chunks neu ein.

## Token-Prüfung

Die REST-API, die Job- und Chat-Streams und `/mcp` teilen nun eine Prüfung, die strenger ist als zuvor.

- `auth.issuer` muss eine `https`-URL sein, außer sein Host ist `localhost`, eine Subdomain davon, `127.0.0.1` oder `::1`.
- Ein Token muss `exp` und `iat` tragen.
- Es muss mit einem asymmetrischen Algorithmus signiert sein, RS256, RS384, RS512, PS256, PS384, PS512, ES256, ES384, ES512, EdDSA oder Ed25519, und nur die davon, die der Anbieter in seinem Discovery-Dokument ankündigt, werden akzeptiert.
  Mit `none` oder einem HMAC-Algorithmus (HS256, HS384, HS512) signierte Tokens werden abgelehnt.
- Der `issuer` des Discovery-Dokuments muss `auth.issuer` bis auf einen abschließenden Schrägstrich gleichen, und das `iss` jedes Tokens muss dem `issuer` des Discovery-Dokuments Byte für Byte gleichen.
- Die `jwks_uri` des Discovery-Dokuments muss auf dem Origin des Issuers liegen, Schema eingeschlossen, sodass ein `https`-Issuer seine Schlüssel per `https` ausliefert.
- Ein Token muss ein Access-Token sein, ausgewiesen durch den Header `typ: at+jwt` nach RFC 9068 oder, wie Keycloak und Rauthy es ausstellen, durch den Claim `typ: Bearer`, sodass ein ID-Token desselben Clients abgelehnt wird.
- Ein Token muss seinen Client in `azp` oder `client_id` nennen, und beide müssen übereinstimmen, wenn beide vorhanden sind, sodass ein Token der Bridge nie als das einer Person durchgeht.
- Der Ablauf eines Tokens erhält 60 Sekunden Toleranz für Uhrabweichungen statt fünf Minuten.
- Die zwischengespeicherten Signaturschlüssel bleiben bei unerreichbarem Issuer höchstens `auth.jwks_max_age_seconds` nach ihrem letzten erfolgreichen Abruf vertrauenswürdig, standardmäßig sechs Stunden.
- `auth.audience` listet exakte Client-IDs, ist Pflicht und lehnt Platzhalter ab, ersetzen Sie also ein Muster wie `hivegent-*` durch jede Client-ID, die es traf.
- Ein Token für `/mcp` muss `mcp.base_url`, genau so geschrieben wie konfiguriert, oder, falls gesetzt, `mcp.client_id` als Audience nennen.
  Den Grund, aus dem ein Token abgelehnt wird, protokolliert der Server.

Langlebige Streams, der Job-Feed und ein laufender Chat-Zug, enden nun mit dem Ablauf ihres Tokens, und die App verbindet sich mit einem frischen neu, sodass eine entzogene Gruppenmitgliedschaft oder Admin-Rolle dann wirkt.
Ein laufender Chat-Zug und ein eingereihter Dokumentauftrag handeln mit den Berechtigungen des Tokens, das sie startete, das jede spätere Anfrage desselben Benutzers verlängert, wie es die geöffnete App über ihre Streams tut.
Ein Token, das ihre Berechtigungen nicht mehr hat, etwa nach dem Entzug einer Gruppe, verlängert keinen von beiden, sodass ein Zug eine Minute nach Ablauf des jüngsten verlängernden Tokens endet und speichert, was er erreicht hat, und ein eingereihter Auftrag dann ohne Start fehlschlägt.

## MCP-Endpunkt

`mcp.base_url` muss die öffentliche URL des Endpunkts sein, deren Pfad genau `/mcp` ist, etwa `https://hivegent.example.com/mcp`, was der Start prüft.
Der Endpunkt bedient zustandsloses HTTP, ohne Sitzungen und ohne GET-Stream.
Er antwortet nun nur noch unter `/mcp`, nicht mehr unter `/mcp/`, entfernen Sie also einen abschließenden Schrägstrich aus der URL jedes MCP-Clients.
Er kündigt `mcp.base_url` genau so wie konfiguriert als seine Ressource an und stellt seine OAuth-Discovery-Dokumente an der Wurzel bereit, etwa `/.well-known/oauth-protected-resource/mcp`, die der mitgelieferte Caddy weiterleitet, daher muss auch ein eigener Reverse-Proxy vor dem Backend sie weiterleiten.
Der Callback des Modus `proxy` bleibt `<mcp.base_url>/auth/callback`.

## Ratenbegrenzung und ausgehende Anfragen

Jede authentifizierte Anfrage, an die REST-API und an `/mcp`, verbraucht nun ein Token des neuen Kontingents `rate_limits.request`, und der Wartungsmodus sperrt Nicht-Admins nun auch aus `/mcp` aus.
Neukonvertierungen, Neuchunking, Dokument-Schreibvorgänge und Workspace-Änderungen verbrauchen nun das Kontingent `upload`, Fallbasis-Abfragen und Gesprächsarchive das Kontingent `generation`.
Ein Chat-Zug verbraucht das Kontingent `chat`, die Antwort auf Freigaben oder Fragen, auf die ein Zug wartet, dagegen das größere Kontingent `resume`, da sie den Zug nur fortsetzt.
Die Kontingente liegen im Speicher des Servers, und ein aufgefülltes Kontingent wird vergessen.
`jobs.max_owner_jobs` (Standard 20) und `jobs.max_owner_spool_bytes` (Standard 2 GiB) begrenzen die unfertigen Upload-Jobs einer Person und die Bytes, die diese zwischengespeichert auf der Platte halten, wer eine überschreitet, erhält ein 429.
Das Löschen einer Person bricht zuvor ihre Jobs ab.

Betreiber-Endpunkte erreicht nun ein direkter Client, der auf ihren eigenen Origin beschränkt ist.
Ein externer MCP-Server mit OAuth-Client-Credentials erreicht seinen eigenen Origin, den von `oauth.issuer` und die Origins der Endpunkte, die die Discovery-Metadaten des Issuers selbst nennen, etwa seinen Token-Endpunkt.
`host.http_client()` eines Plugins ist nun der Web-Client, der über den Egress-Proxy nur die öffentlichen Hosts erreicht, die `security.web_urls` erlaubt.

## Teams-Bridge

Die Bridge läuft nicht mehr ohne `auth.service_client_id`.
Ihre `backendUrl` muss `https` sein, sofern sie keinen Loopback-Host nennt.
Beim Start prüft sie, dass ihr Token sie als Service-Principal authentifiziert, und startet andernfalls nicht, da sich sonst alle Teams-Benutzer:innen ein gewöhnliches Konto mit dessen Gedächtnis und Unterhaltungen teilen würden.
Sie braucht außerdem eine Allowlist der Microsoft-Teams-Tenants, die sie bedient, und lehnt Aktivitäten aus jedem anderen Tenant ab.

Die Bridge liest nun alle Einstellungen aus einer streng geprüften JSON-Datei, `BRIDGE_CONFIG_FILE` (Standard `config.json`), und nur ihre Secrets aus der Umgebung oder ihren Credentials: `POSTGRES_URL`, `OIDC_CLIENT_SECRET` und `TEAMS_APP_PASSWORD`.
Der Bot ist nun ein schreibgeschützter Dienstprinzipal, der den globalen Arbeitsbereich und die Quellen ohne `groups` sieht, nicht mehr die Gruppenarbeitsbereiche, die sein Groups-Claim gewährte.
Verschieben Sie eine Wissensbasis, aus der er antwortete, etwa `@team-kb`, vor dem Update in den globalen Arbeitsbereich oder deklarieren Sie sie als Quelle ohne `groups`, da seine Teams-Nutzer sonst nichts finden.

Aus `HIVEGENT_URL` wurde `backendUrl`, aus `ALLOWED_TENANTS`, `TEAMS_APP_ID` und `TEAMS_APP_TENANT_ID` wurden `teams.allowedTenants`, `teams.appId` und `teams.tenantId`, und `HOST`, `PORT`, `BOT_USERNAME`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_SCOPE` und jedes `ENABLE_*` wurden zu Schlüsseln der Datei, wobei `adapters.teams` nun Pflicht ist.
`REASONING_EFFORT` entfällt, da der Bot mit dem Denkaufwand läuft, den hivegent für sein Dienstkonto speichert, dem Serverstandard.
Beim Start wiederholt die Bridge nun bei einem Netzwerkfehler, einer Zeitüberschreitung, einem 5xx oder einem 429 ihre Token-Anfrage an den Identitätsanbieter oder `GET /api/v1/principal` bis zu 5 Minuten lang, mit einem Abstand von 1 bis 30 Sekunden, während jeder andere Fehler, etwa ein Token, das kein Service-Principal ist, sie weiterhin sofort beendet.
Auf einem gemeinsamen NixOS-Host startet ihre Unit nun nach `hivegent.service` und zieht ihn mit, und im Container beendet eine gescheiterte Bridge den Container nicht mehr.
Ein übrig gebliebenes `HIVEGENT_URL` in einer Umgebung, die das Backend teilt, etwa einer gemeinsamen Container-Umgebung für beide, verhindert nun den Start des Backends, da es keine Einstellung benennt.

## Kommandozeilen-Client

Die Client-Befehle des Kommandozeilenwerkzeugs `hivegent`, `login`, `logout`, `upload`, `download`, `list`, `delete` und `whoami`, wurden bewusst entfernt.
Löschen Sie die von `login` hinterlassene `~/.config/hivegent/credentials.json`, da sie das Client-Secret im Klartext enthält.
Auch `hivegent serve --allow-unsafe-auth-disabled-bind` ist entfallen, da `hivegent serve` immer authentifiziert.
Skripten Sie dasselbe über die REST-API oder den MCP-Endpunkt, wie [Skripte](setup.md#skripte) zeigt.
Das Löschen der Daten einer anderen Person, einer Gruppe oder des ganzen Deployments läuft nur auf dem Host mit `hivegent reset`.
Admins können nicht mehr als andere Person handeln, da der Header `X-Impersonate-User` entfallen ist.
Die Debug-Konsolen für Tools und Chats sind standardmäßig aus, `debug.enable = true` bietet sie Admins wieder an, und der Chat-Debugger öffnet nur ihre eigenen Unterhaltungen.

## Dokumentkonvertierung

- Pipeline-Optionen setzt nur noch der Betreiber, je Pipeline-ID in `conversion.pipelines` und `chunking.pipelines`, sodass ein Upload keine Pipeline nennt, eine Anfrage mit `config` mit 422 scheitert und die Einstellungsseite sie nicht mehr bearbeitet.
- Die Konverter `marker` und `mineru` und die Chunker `fast`, `code`, `neural` und `late` sind entfallen, und ein Dokument, das einen davon verzeichnet, behält seine Chunks, bis es sich ändert oder neu gechunkt wird, was dann den Standard nutzt.
- Die Chunker-Optionen `chunk_size` und `chunk_overlap` heißen nun `size` und `overlap`.
- `.dot`-, `.pot`- und `.xlt`-Dateien werden als Klartext gelesen, statt als Office-Vorlagen konvertiert.
- Ein hochgeladener Ordner oder ein ZIP mit einem einzigen Ordner auf oberster Ebene behält diesen Ordner, statt ausgepackt zu werden.
- `compute.device` legt fest, worauf die Modelle von docling, das lokale Embedding-Modell und der lokale Reranker laufen, standardmäßig `cpu` statt eines erkannten Beschleunigers, und `mps` ist die einzige dokumentierte Alternative, für die Entwicklung unter macOS.
- Linux-Builds laufen nur noch auf der CPU: Der CUDA-Build von torch ist entfallen, das NixOS-Modul hebt `PrivateDevices` nicht mehr für die NVIDIA-Geräte auf, und der auskommentierte `nvidia`-Block der `compose.yaml` ist entfernt, entfernen Sie also eine eigene GPU-Reservierung.
- Konvertierungen und der Chunker `semantic` laufen in Worker-Prozessen, die übrigen Chunker, die kein Modell laden, dagegen in einem Thread des Servers, sodass sie nie hinter Konvertierungen warten.
- Eine Aufgabe eines Workers endet nun nach `compute.task_timeout_seconds` (Standard 1800), und ihr Worker wird ersetzt, sodass ein hängender Konverter den Pool nicht mehr blockiert, und ein Dokument, das die Zeit überschreitet, fällt auf reine Textextraktion zurück.
  Ein Worker wird außerdem nach `compute.worker_max_tasks` Aufgaben (Standard 200) gegen Speicherlecks ersetzt, was seine Modelle neu lädt, und beides ist `null` für keine Grenze.
- `pandoc` und `pdftotext` werden ab 4 GiB Speicher gestoppt, `pandoc` auch ab 256 MiB aus einer Datei extrahierter Bilder, seinem `max_assets`, das zugleich sein Limit für das Schreiben einer Datei ist, `pdftotext` ab 256 MiB Ausgabe, und beide sehen von der Umgebung nur `PATH`, `LANG` und `LC_ALL`.
- Der OCR-Cache von Kreuzberg ist abgeschaltet, und `XDG_CACHE_HOME` ist unter NixOS und im Container `/var/cache/hivegent`.
- Standbilder, die das View-Tool liest und die an den Chat angehängt werden, werden auf 1024 px verkleinert.
- Jede temporäre Datei liegt unter `<data_dir>/.transient`, dem temporären Verzeichnis des Servers und seiner Worker, das der Start leert.
- Der Chunker `markdown` teilt einen Abschnitt, eine Tabelle oder einen Codeblock, der länger als seine `size` (Standard 2048) ist, und behält die Bilder eines Abschnitts, sodass ein damit gechunktes Dokument anders gechunkt wird, sobald es sich ändert oder neu gechunkt wird.

## Konversationsspeicher

Der erste Start schreibt jede Nachrichten-ID der Datenbank neu, da der Konversationsspeicher eigene vergibt, und die Migration lässt sich nicht umkehren.
Vor dem Start der neuen Version Datenbank und Datenverzeichnis gemeinsam sichern, der einzige Rückweg ist das Backup, und beide gemeinsam wiederherstellen, da die Dateien auf der Platte und die Zeilen einander beschreiben.
Konversationen behalten ihre IDs, Links funktionieren also weiter.
Zur Freigabe vorgemerkte Änderungen, die den Upgrade überdauern, werden verworfen und gelten als abgelaufen, sie müssen erneut angefordert werden.
Anfragen speichern ihren System-Prompt nun nur noch, wo er sich von dem vor ihnen in ihrer Konversation unterscheidet, was den Speicherbedarf der Konversationen etwa halbiert.
Die Migration schreibt jede Anfragezeile neu.
Diese einmaligen Umschreibungen lassen `messages` bis zum Aufräumen etwa doppelt so groß.
Führen Sie nach dem ersten Start einmal `VACUUM (ANALYZE) messages` aus, damit der Platz wiederverwendbar wird, was die Unterhaltungen verfügbar hält.
`VACUUM FULL messages` oder pg_repack gibt ihn an das System zurück, doch das erste sperrt die Unterhaltungen währenddessen, planen Sie dafür also Ausfallzeit ein.
Eine `.env`-Datei wird nicht mehr gelesen, ihre Werte gehören in die Umgebung, in Credentials oder in `config.toml`.

## Prompt-Stabilität

- Der System-Prompt enthält nicht mehr das Datum, es ist ein angehängter Hinweis wie der Modus-Hinweis, sodass das Prompt-Präfix einer Unterhaltung und sein Provider-Cache über Mitternacht gültig bleiben.
  Ein Wechsel von Modus, Tools, Assistent, Modell oder Sprache zwischen Zügen ändert es weiterhin, ebenso der Ausfall eines externen MCP-Servers, der die Tool-Liste ändert.
- Ein Subagent nach seiner Berichtsfrist behält seine Tools und Anweisungen, und seine weiteren Tool-Aufrufe werden mit einem Ergebnis abgelehnt, das ihn zum Berichten auffordert.
- Der Abschlussbericht eines Subagenten wird auf `llm.tool_output_max_chars` gekürzt, der ganze Text liegt unter `/tmp/.tool-results/`, wie bei jedem Tool-Ergebnis.
- Skill-Anweisungen werden, wie auch immer geladen, auf `llm.tool_output_max_chars` gekürzt und verweisen auf den Rest in `SKILL.md`.
- Ein importiertes Archiv wird auf `multimodal.max_images` Bilder je Nachricht und `llm.tool_output_max_chars` je Tool-Ergebnis begrenzt.
- Die Freigabe-Zusammenfassung eines Changesets wird einmal gespeichert, an der Antwort, die gefragt hat, und jeder gespeicherte Zug oder jede Verdichtung behält nur die Changesets, auf die ein ausstehender Aufruf von `apply_changes` noch wartet.

## Datenbank-Pooling und Neustarts

Ein laufender Server hält einen PostgreSQL-Advisory-Lock auf Sitzungsebene, daher muss ein PgBouncer vor der Datenbank Session Pooling nutzen.
Die neuen `db.pool_size` (Standard 10), `db.max_overflow` (Standard 10) und `db.pool_timeout_seconds` (Standard 30) bemessen den Verbindungspool jedes Servers, dessen Sperren eigene Verbindungen außerhalb davon nutzen, stellen Sie daher sicher, dass `max_connections` von PostgreSQL `pool_size + max_overflow` plus etwa 2 für jeden Server abdeckt.

Ein Server, der die Verbindung seiner Sperre verloren hat, beendet sich nun, und sein Dienst startet ihn neu.
Die NixOS-Unit startet immer neu, mit einem Abstand von 5 bis 60 Sekunden und höchstens 20-mal in 10 Minuten, und der Container startet das Backend mit mindestens 10 Sekunden Abstand neu, höchstens 60-mal in 10 Minuten.
Ein Stopp durch den Betrieb, etwa mit `systemctl stop`, `dinitctl stop` oder für `hivegent-manage`, wird nicht neu gestartet.

## Selbst betriebene Laufzeiten, Kostengrenzen und MCP-Schlüssel

- `inference_provider = "openai"` bedeutet nun OpenAIs eigene API, setzen Sie daher `sglang`, `ollama` oder `openai-compatible` für jeden anderen Server, der es nutzte, damit seine Modelle nicht wie OpenAI-Modelle bepreist und tokenisiert werden und SGLang und Ollama die Steuerung des Reasonings erhalten.
- Ein selbst betriebener Anbieter trägt nun in Traces und gespeicherten Antworten den Namen seiner Laufzeit, etwa `llama.cpp`, wo zuvor `openai` stand.
- Bei einem Modell eines `llama.cpp`-Anbieters wird die Familie nicht mehr aus dem bereitgestellten Namen erraten, daher setzen Sie `llm.models.<id>.family`, etwa `qwen`, um seine Behandlung von Werkzeugen und Schemas zu behalten.
- `family` wird bei einem Anbieter `vllm`, `ollama` oder `openai` abgelehnt, die die Familie am bereitgestellten Namen erkennen, und ein `llama.cpp`-Modell namens `<Hersteller>/<Modell>`, etwa `Qwen/Qwen3-32B`, erhält ohne sie die Familie seines Herstellers.
- `subagents.max_cost` zählt nur Modelle, die pydantic-ai bepreisen kann, ein selbst betriebenes begrenzen daher Anfrage- und Token-Grenzen.
- `sandbox.execution_clock` ist standardmäßig `cpu`, was Monty nur unter Linux misst, daher setzt ein Server auf einem anderen System `wall`.
- `/mcp` braucht das Secret `hivegent.MCP__CONFIRMATION_KEY` mit mindestens 32 Zeichen, das die Bestätigungen seiner Clients signiert, sodass sie über Neustarts hinweg gelten, und ohne es startet der Server nicht.

## Agenten-Tools

- An Unteragenten wird über `delegate_task` und seine Aufgaben-Tools delegiert statt über das Tool `explore`, sodass ein Eintrag in `tools.disabled`, der `explore` als Tool nennt, nun den Unteragenten `explore` zurückhält.
- Der MCP-Endpunkt stellt das Unteragenten-Tool `explore` nicht mehr bereit, ein MCP-Client ruft die Explore-Tools selbst auf.
- Ein Schreibzugriff, der keine Änderung am Arbeitsbereich ist, etwa `write_memory`, ein Plugin-Tool oder ein externes MCP-Tool, fragt nun auch im Modus `write` nach Bestätigung.
- Suchergebnisse tragen keinen `score` mehr, da er sie nur ordnete, und ihre Reihenfolge ist die Rangfolge.
- Die Gesprächs-Tools `list_conversations`, `get_conversation` und `search_conversation` werden standardmäßig angeboten, nennen Sie sie also in `tools.disabled`, um vergangene Unterhaltungen außer Reichweite zu halten.

## Einträge des Dokumentfilters

Eine Nachricht der Person nennt die Dokumente, um die es geht, in ihren Metadaten, `{"documents": {"included": [...], "excluded": [...]}}`, statt `included_documents` und `excluded_documents` der Chat-Anfrage, und jeder Eintrag ist ein Objekt aus einem Pfad und einem Merkmal `directory`, etwa `{"path": "~/handbuch", "directory": true}`, statt einer Zeichenkette, deren abschließender Schrägstrich ein Verzeichnis kennzeichnete.

## Entfernte Funktionen

Benutzer:innen können keine Persönlichkeit mehr wählen, keine eigene Systemnachricht schreiben, keine eigenen MCP-Server hinzufügen und kein eigenes Modell mit Endpunkt und Schlüssel mitbringen, ihre gespeicherten eigenen Prompts und Server entfallen daher.
An ihre Stelle treten der Modellkatalog, der MCP-Katalog und die Assistenten des Betreibers.
Auch die Feature-Flags des Frontends sind entfallen und mit ihnen die verborgene Auswahl der Konvertierungs- und Chunking-Pipelines und des Asset-Modus, sodass jeder Upload die Pipelines des Betreibers nutzt, und `conversion.assets` (`ignore`, `store` als Standard oder `describe`) legt fest, wie jede Konvertierung die extrahierten Bilder behandelt.
Tools lassen sich nicht mehr selbst abschalten, ein Assistent oder `tools.disabled` hält sie zurück.

## Präferenzen liegen auf dem Server

Assistent, Modus, Chat-Modell und Denkaufwand, die eine Person wählt, speichert der Server (`GET /preferences`, `PATCH /preferences`), sodass sie über Browser und Geräte hinweg gelten und für jeden Client, auch den Teams-Bot, dessen Dienstkonto eigene hat.
Eine Chat-Anfrage ist nur noch der Body des AI SDK samt `compact` und wird mit 422 abgelehnt, wenn sie ein anderes Feld sendet, etwa `assistant`, `mode`, `model`, `reasoning_effort` oder `disabled_tools`.
Der Browser speichert nur noch die aufgeklappten Ordner, sodass die früheren Einstellungen jeder Person einmalig zurückgesetzt werden.

## REST-API

Die Routen haben sich unter demselben Präfix `/api/v1` geändert.

- `DELETE /documents/{scope}` ist nun `DELETE /workspaces/{scope}`.
- `POST /mcp/test` und `GET /pipelines/{stage}/{pipeline}/config` sind entfallen, da nur der Betreiber MCP-Server und Pipelines konfiguriert, und `GET /pipelines/{stage}` ist nun `GET /admin/pipelines/{stage}`.
- Die Admin-Routen, die die Daten einer Person, einer Gruppe oder alle Daten löschten, sind entfallen, siehe [Kommandozeilen-Client](#kommandozeilen-client).
- Uploads, Sammlungen, Rechunks, Reconverts und das Schreiben von Dokumenten nehmen keine Pipeline mehr entgegen (`pipeline_spec`, `pipeline`, `chunking`), da jedes Dokument mit den Pipelines des Betreibers verarbeitet wird, während jede Quelle ihre eigenen behält.
- `POST /changes` und die Einzelrouten zum Verschieben, Löschen und für Verzeichnisse antworten mit der angewandten Änderung samt einer neuen `id`, unter der der Job-Feed sie auch dem Aufrufer ankündigt, da der Header `X-Client-Id` nicht mehr gelesen wird.
  Ein Client, der die Antwort angewandt hat, überspringt die Ankündigung mit derselben `id`.
