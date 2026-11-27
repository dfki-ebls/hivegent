# Setup

Hivegent ships as one container image, `ghcr.io/dfki-ebls/hivegent`.
A typical deployment runs three containers with Docker Compose: Hivegent, PostgreSQL, and an OIDC identity provider.
The repository's `compose.yaml` is a ready-to-run example with Rauthy.

> **Additional terms of use:** Hivegent is provided under the MIT license and the DFKI's [additional terms of use](terms.md).
> By downloading, installing, or using the software, you accept the additional terms.
> Please read them in full beforehand, the German version is authoritative.

## Prerequisites

- Docker with Compose, or Podman.
- An OpenAI-compatible model endpoint, hosted (such as OpenAI) or local (such as vLLM, SGLang, or Ollama).

## Quick start

1. Copy `compose.yaml` into an empty directory.
2. Read the [additional terms of use](terms.md) and accept them by setting `HIVEGENT_ACCEPT_TERMS` to `true`, Hivegent does not start otherwise.
3. Set the language model and the OIDC issuer as described below.
4. Run `docker compose up -d`, the first start takes a few minutes.
5. Open <http://localhost:8080>.

## Configuration

The backend reads `/data/config.toml`, which the container needs mounted, and `HIVEGENT_*` environment variables, which win.
Nested keys use a double underscore, so `[llm] default` becomes `HIVEGENT_LLM__DEFAULT`.
Mount secrets such as API keys as files `hivegent.<NAME>` for the variable `HIVEGENT_<NAME>` under `/run/secrets`, for example `hivegent.LLM__PROVIDERS__LOCAL__API_KEY` with Compose `secrets`.

```toml
[db]
url = "postgresql+psycopg://hivegent:hivegent@postgresql:5432/hivegent"

[llm]
default = "chat"
aux = "small"

[llm.providers.local]
base_url = "http://your-llm-host:8000/v1"
inference_provider = "vllm"

[llm.models.chat]
provider = "local"
model = "your-chat-model"
label = "Chat"
reasoning = ["low", "medium", "high"]
vision = true

[llm.models.small]
provider = "local"
model = "your-small-vision-model"
vision = true
chat = false

[auth]
issuer = "http://auth.localhost:8081/auth/v1/"
audience = ["hivegent-spa", "hivegent-bot"]
service_client_id = "hivegent-bot"
```

- `llm.providers.<id>`: an endpoint, its `base_url` and `inference_provider`, which is `llama.cpp`, `vllm`, `sglang`, `ollama`, `openai` (default) for OpenAI's own API, `openai-compatible` for any other server, or `system-one` for a server of decision models over the System One API, such as vLLM serving D3.
  Match your server to enable reasoning control and chat-template fixes, and to keep a local model from being priced or tokenized as an OpenAI one.
  llama.cpp and vLLM cap reasoning at `llm.thinking_budget_tokens`, SGLang and Ollama only switch it on and off.
  Its API key, if it needs one, is the secret `hivegent.LLM__PROVIDERS__<ID>__API_KEY`.
- `llm.models.<id>`: a model its `provider` serves as `model`, shown as `label`.
  `reasoning` lists the efforts the model implements, which users may pick, so leave it unset for a model that takes any effort, list the levels of one that implements only some, and set `[]` for one that does not think, which hides the selector and turns thinking off, `vision` allows image attachments, and `max_tokens` and `context_window` give its limits.
  `max_images` is the most images its server takes in one request, such as vLLM's `--limit-mm-per-prompt`, and defaults to `multimodal.max_images`, which is unlimited unless set.
  Older images of a conversation are left out of a request beyond it, and a turn may attach no more.
  `reasoning_overrides` sends another effort for a picked one, such as `{ high = "xhigh" }` for Qwen3.8, which implements `xhigh` in place of `high`.
  `chat = false` keeps a model out of the chat, such as one only for a role below.
  On a `llama.cpp`, `sglang`, or `openai-compatible` provider, `family` names the model family for its tool and schema quirks: `llama`, `gemma`, `qwen`, `deepseek`, `mistral`, `cohere`, `gpt-oss`, or `glm`.
  Without it, a served name such as `Qwen/Qwen3-32B` selects the family of its vendor, while vLLM and Ollama tell the family from the served name themselves and refuse `family`.
- `llm.default`: the chat model a conversation starts on, which needs a large context and tool calling.
  Users pick among the chat models when there are several.
- `llm.aux`: a small, fast, vision-capable model for conversion, captions, and titles, always with thinking off.
  Setting it also titles new conversations and describes the images extracted from documents automatically, which otherwise happens only on request.
  Everything that answers the user, including subagents, compaction, case bases, and the sandbox's `complete`, runs on the chat model with the chosen reasoning effort.
  Defaults to `llm.default`.
- `llm.stt`: a transcription model such as `whisper-1` for browsers without speech recognition, off when unset.
- `llm.decision`: the model that answers yes/no questions with a probability, off when unset.
  Setting it offers the `markdown-coref` chunker, which joins chunks that refer back to the one before them.
  Any model of the catalog judges, a fast chat model as well as a decision model of a `system-one` provider, which answers faster and with calibrated probabilities but fills no other role, so it needs `chat = false`.
- `subagents.max_cost`: the most a chat turn may spend in US dollars, its subagents included, also those still running after it, unbounded when unset.
  It counts only models pydantic-ai can price, so a self-hosted one is bounded by `llm.request_limit` and the token limits alone.
- `subagents.max_steps`: how many model requests a subagent makes before it is told to report what it found, 30 by default.
- `embedding.endpoint`, `rerank.endpoint`, and `transparency.endpoint`: the provider a remote embedder (`embedding.provider = "openai"`), reranker (`rerank.provider = "http"`, its `/rerank` route), or watermark detector (at `transparency.detector_path`) is served from, sharing that provider's key.
  A path is resolved against the provider's `base_url` like a link, below it if relative, such as `rerank`, and on its origin if absolute, such as llmhop's `/llmhop/proxy/watermark/detect`.
  A remote embedder also needs `model` and its output size `dimension`.
  `embedding.query_prefix` and `embedding.document_prefix` prepend what the model expects, such as `"query: "` and `"passage: "` for e5 models, or an instruction such as `"Instruct: Given a question, retrieve passages that answer it\nQuery:"` before the queries of Qwen3-Embedding models.
  `embedding.request` is passed to every request, such as `extra_body = { truncate_prompt_tokens = -1 }`, with which vLLM truncates over-long texts itself.
  Only OpenAI's own models are cut to their 8192 tokens client-side, since only their tokenizer is known, so another server must truncate over-long texts itself or refuses them.
- `db.url`: the PostgreSQL connection string.
  The bundled database has pgvector enabled.
- `db.pool_size`, `db.max_overflow`, and `db.pool_timeout_seconds`: the connections a server keeps open (default 10), the ones it opens beyond them under load (default 10), and how long a request waits for one before it fails (default 30 seconds).
  The server's locks hold connections of their own outside the pool, so size `max_connections` of PostgreSQL for `pool_size + max_overflow` plus about 2 per server.
- `auth.issuer`: the OIDC issuer URL, which `hivegent serve` requires to start, served over `https` unless its host is `localhost`, a subdomain of it, `127.0.0.1`, or `::1`.
  It must name the issuer of the provider's discovery document, a trailing slash aside, whose exact spelling the tokens' `iss` must then match.
  Only `hivegent dev`, the local development server, runs without it, binding the loopback address and acting as a development user for every request.
- `auth.audience`: accepted token audiences, the exact ids of the clients allowed to call the API, such as the SPA and the bridge bot.
- `auth.service_client_id`: the client of the Teams bridge, whose tokens read only the global workspace, only in read mode, without memory or conversation tools, and reach only the chat route.
  The bridge refuses to start unless the backend names its token a service principal, and answers only the Teams tenants its `teams.allowedTenants` lists (`bridge/README.md`).

Common extras are `HIVEGENT_TOOLS__SEARCH_URL` for web search, `HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS='["*"]'` for web fetch, `HIVEGENT_EMBEDDING__MODEL` for the embedding model, and `HIVEGENT_MCP__BASE_URL`, the endpoint's public URL, whose path must be exactly `/mcp`, such as `https://hivegent.example.com/mcp`, for the MCP endpoint, which needs the secret `hivegent.MCP__CONFIRMATION_KEY`, a random string such as `openssl rand -hex 32` that signs the approvals of its clients, and whose default `proxy` mode also needs `HIVEGENT_MCP__CLIENT_ID` and the secret `hivegent.MCP__CLIENT_SECRET`.
`HIVEGENT_DOCS_URL` names where this handbook is served, such as `/docs/`, which shows a link to it in the app, and the NixOS module and the container set it from where they serve the handbook, if they bundle one.
In the container, `-e HIVEGENT_DOCS_URL=https://docs.example.com/` points the link at a handbook served elsewhere.

`tools.disabled` withholds tools from the model by name, such as `web_search`, and is the only way to withhold one, since neither users nor requests can.
It is empty by default, and a withheld tool is withheld from the subagents too, so naming the conversation tools also switches off the `conversations` subagent.
An unknown name stops the server at startup with an error listing the valid ones.

`HIVEGENT_RAGOLD__URL` names where a RAGold instance is served, which lets users transfer a reply with its documents as a gold benchmark entry.
`HIVEGENT_RAGOLD__PROJECT` names the RAGold project these entries are filed under (default `Hivegent`).

Skills are folders of instructions and scripts following the [Agent Skills](https://agentskills.io) format, which the assistant loads for matching tasks.
`skills.dir` names a directory of global skills, one folder with a `SKILL.md` each, which every user sees under `/skills/srv` and nobody changes through Hivegent, so it may lie in the Nix store.
Users install or write their own and their groups' skills in the settings, as ZIP archives or a `SKILL.md`, kept in `<data_dir>/skills/`, each at most `skills.max_files` files (default 512) in `skills.max_bytes` (default 16 MiB).
Skill scripts run in the Python sandbox, so only scripts written for it, defining a `main` function, run, while the others stay readable.
A skill is no document, so the document tools never reach it, the assistant reads its files only by loading the skill, and it never writes one, only people change skills.

A skill of a group runs with the grants of whoever uses it, never with those of its author.
So it reaches nothing its reader could not reach alone, but a writer of a group's library steers what the runs of its readers do within their grants, for example which documents they read or which changes they propose.
Grant write access to a group only to people its readers trust with that, and review changes to its skills like code.

`llm.instructions` holds your own instructions for every chat, such as house rules or a tone, added to the system prompt with the authority of your configuration, for all users alike.
It is empty by default.
Users add their own under Settings, Instructions, which reach the assistant as the user's own words and cannot override its rules.

[Plugins](plugins.md) add tools, converters, chunkers, and skills, enabled by name in `plugins.enabled`.
The Nix build installs them from their project directories into the backend's virtualenv, so the container image and the NixOS module take them as a build input, not at runtime.

`mcp_servers.<id>` connects the assistant to a remote MCP server, whose tools it is offered as `<id>_<tool>`.
The id holds lowercase letters, digits, and hyphens and must not start the name of a built-in tool, such as `read`, `web`, or `delegate`, the delegation tools of the subagents included even while they are off, which stops the server at startup.
Entries follow the notation of MCP client configs such as `.mcp.json` and Claude Desktop: `type` is `"http"` (or `"streamable-http"`) for a Streamable HTTP endpoint and `"stdio"` for a server the backend starts as a subprocess, and without it a `command` means stdio and a `url` HTTP.
Each takes only the fields of its own type, and `sse` is refused.
The standard fields `command`, `args`, `env`, `cwd`, `url`, and `headers` keep their names and meaning, and everything else here is an addition.

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

- `url`, HTTP only: the Streamable HTTP endpoint.
- `headers.<name>`: a static header with a plain value.
- `secret_headers.<name>`: a static header such as an API key, given as the secret `hivegent.MCP_SERVERS__<ID>__SECRET_HEADERS__<NAME>`.
- `oauth`: OAuth client credentials, whose `client_secret` is the secret `hivegent.MCP_SERVERS__<ID>__OAUTH__CLIENT_SECRET` and is sent only to the authorization server `issuer` names.
- `groups`: the groups whose members see the server, everyone and the Teams bridge when empty.
- `allowed_tools` and `blocked_tools`: the tools offered, by their names on the server, all when `allowed_tools` is unset.
- `effects`: what tools may do by their names on the server, `read`, `write`, or `execute`, which overrides their hints.
- `trusted`: whether the server's tool hints count (default `false`).
  A tool hinted read-only reads, one hinted neither destructive nor open-world writes, and any other executes.
  Every other tool of an untrusted server executes.
  A trusted server's instructions also reach the assistant, with the authority of your own configuration.
- `timeout_seconds`: the bound on connecting and on every request (default 30).
- `refresh_seconds`: how often the server is asked for its tools again, to take up a change it does not announce (default 300).
- `forward_user`, HTTP only: whether every tool call names its caller to the server in the header `X-Hivegent-User` (default `false`), a person by the `sub` claim of their token and the Teams bridge by `auth.service_client_id`.
  Nothing else about the user is sent, no name, email, or groups, and listing the tools names nobody.
  Each such call opens a connection of its own, since the header belongs to the caller.

The `sub` claim is pseudonymous but still personal data under the GDPR, since the identity provider links it to a person.
Enable `forward_user` only for a server that needs to attribute calls, such as for its own access control or audit log, record the transfer in your records of processing with its purpose and legal basis, and conclude a data processing agreement where another party operates the server.

A stdio server such as a community package is started with `command` and `args`, so the command must be on the `PATH` of the backend, and `cwd` sets its working directory.

```toml
[mcp_servers.git]
command = "uvx"
args = ["mcp-server-git", "--repository", "/mnt/handbook"]
groups = ["sales"]

[mcp_servers.git.env]
LOG_LEVEL = "warning"
```

This is the TOML form of the `mcpServers` entry `{"git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "/mnt/handbook"], "env": {"LOG_LEVEL": "warning"}}}`, plus `groups`.

- `env.<name>`: an environment variable with a plain value.
- `secret_env.<name>`: an environment variable such as an API key, given as the secret `hivegent.MCP_SERVERS__<ID>__SECRET_ENV__<NAME>`.

The process gets only these variables and a minimal base (`PATH`, `HOME`, and a few more), never the environment of the backend, which holds its secrets.
It is started with the backend, started again after a failure like any other server, and ended when the backend stops.
It runs with the privileges of the backend and outside Hivegent's egress confinement, so it can reach any address the host allows, and only an operator can declare one.
Install only servers you trust, and set `trusted` only when you trust their hints too.
With the NixOS module, `services.hivegent.mcpPackages` puts the packages providing the command on the service's `PATH`, such as `[ pkgs.uv ]`, and a server that needs the network, which includes `uvx` and `npx` downloading a package, needs `allowedAddresses` to cover its addresses.

A tool that reads runs in every mode, any other is not offered in Read mode and asks before every call, which leaves the Teams bridge only the reading tools of servers without `groups`.
Every call acts with the deployment's credentials rather than the user's, so grant a server only what every member of its `groups` may do.
Hivegent keeps one connection per server (one process for stdio) and reconnects with a growing backoff, and while a server is unreachable its tools are withheld with a short note to the assistant instead of failing the reply.
Admins see each server's status and its tools' effects under Administration, Integrations.

`databases.<id>` lets the assistant explore and read a SQL database and run the statements the operator wrote and `apis.<id>` lets it call the operations of an HTTP API, as tools named `<id>_<tool>` with the same id rules as an MCP server's, which are shared, so no id names two of them.

```toml
[databases.erp]
url = "postgresql+psycopg://hivegent@db.example.com:5432/erp"
tables = ["orders", "customers"]
groups = ["sales"]
instructions = "Orders of the last five years, amounts in euro."

[databases.erp.statements.close_order]
description = "Close an order by its id."
sql = "UPDATE orders SET closed = true WHERE id = :id"
params = { id = "integer" }
effect = "write"

[apis.crm]
url = "https://crm.example.com/api/v1"
spec = "/etc/hivegent/crm-openapi.yaml"
operations = ["getContact", "addNote"]
headers = {}
```

- `databases.<id>.url`: a SQLAlchemy URL without its password, which is the secret `hivegent.DATABASES__<ID>__PASSWORD`, and the driver of its dialect must be installed, which is checked at start.
  PostgreSQL, MySQL, and SQLite are supported.
- `tables`: the tables and views the assistant lists, describes, and reads through `<id>_select`, and no others.
  The assistant never writes SQL, a selection names a table, columns, filters, groups, aggregates, and an order, which are checked against the table and compiled with every value bound.
  A join is a view the operator creates and lists here.
- `statements.<name>`: a statement the operator wrote, offered as the tool `<id>_<name>` with its `description`, whose `sql` binds each parameter as `:name` and whose `params` give their types, `string`, `integer`, `number`, `boolean`, `date`, or `datetime`.
  A statement whose parameters differ from those its `sql` binds stops the server at startup.
  `effect = "read"` (the default) runs it in a read-only transaction, `effect = "write"` commits it and asks before every call.
  A database needs `tables`, `statements`, or both.
- `max_rows` and `timeout_seconds`: the rows a selection or statement returns (default 100) and its statement timeout (default 20).
  The privileges of the database account still bound what a statement touches, so grant it what the tables and statements need alone.
- `apis.<id>.url` and `spec`: the base URL requests go to, whose origin is the only one they may reach, and the path of the OpenAPI 3 document, JSON or YAML, whose `$ref`s into its own `components` are resolved.
- `operations`: the `operationId`s offered, each as a tool `<id>_<operationId>` with the parameters of the operation, and an unknown id stops the server at startup.
  Header and cookie parameters are not offered, the deployment authenticates instead.
- `headers.<name>`: a static header with a plain value, such as `OCS-APIRequest = "true"` for the OCS API of Nextcloud.
- `secret_headers.<name>`: a static header such as an API key, given as the secret `hivegent.APIS__<ID>__SECRET_HEADERS__<NAME>`.
  A name is either plain or secret, not both.
- `max_response_bytes` and `timeout_seconds`: the bytes of a response that are read (default 1 MB) and the bound on a request (default 20).
- `groups` and `instructions`: the groups whose members see it, everyone and the Teams bridge when empty, and what the assistant is told about its data or when to use it.

A selection, a read statement, and a `GET` or `HEAD` operation read and run in every mode, a write statement and any other method are not offered in Read mode and ask before every call, which leaves the Teams bridge only the reading tools of sources without `groups`.
Like the tools of an MCP server, they are not tied to a person or to the workspaces, so they are offered whichever documents a chat selects.
Every call acts with the deployment's credentials rather than the user's, so grant an account or API key only what every member of the `groups` may do.
Admins see each one's host, groups, and tools' effects under Administration, Integrations.

`sources.<id>` mirrors an external file store, such as a Nextcloud folder, into Hivegent, where the members of its `groups` find it at `/mnt/<id>` and search, cite, and preview it like any workspace.
Only this configuration declares sources, with their secrets in credential files, so no user or group can add one or reach its credentials.

```toml
[sources.handbook]
connector = "webdav"
url = "https://cloud.example.com/remote.php/dav/files/hivegent/Handbook/"
username = "hivegent"
groups = ["sales"]
interval_minutes = 15
```

- `connector`: how the source is reached, `webdav` built in for WebDAV servers such as Nextcloud and `local` for a mounted directory, others contributed by enabled [plugins](plugins.md).
- `url`: the root of the source, required by `webdav`, whose host, or one listed in `hosts`, is the only one a request may reach.
- `username` and `password`: Basic authentication, the password given as the secret `hivegent.SOURCES__<ID>__PASSWORD`.
  For Nextcloud, share the folder with a dedicated account, create an app password for it under Settings, Security, and point `url` at the folder below `/remote.php/dav/files/<account>/`.
- `groups`: the groups whose members see the source, everyone and the Teams bridge when empty.
- `interval_minutes`: how often the source syncs (default 15).
  A file the source reports deleted, as Nextcloud does, or one missing from a complete listing is deleted at the next sync, as rclone does, since the mirror is a cache and a wrong deletion costs only a new download.
  A listing the source refused in part deletes only the files it reports deleted.
- `max_sweep_share`: the share of the mirror one sync may delete for files the source lost, missing from a listing or no longer mirrorable (default 0.5), above which, and above 10 files, or when a listing comes back empty, those deletions wait for an admin to confirm under Administration, Sources, since an outage looks like an empty folder.
  Held files stay in the mirror until the source lists them again or an admin confirms, and files the source reports deleted are never held, while the rest of the sync lands.
- `options`: the connector's own settings, for `webdav` `max_depth`, the deepest folder level below `url` that is listed (default 32), since a deeper one fails the sync, and `max_file_size_bytes`, the largest file that is read (default 50 MiB).
- `conversion` and `chunking`: the ids of the pipelines its files are converted and chunked with (default `auto`, as for an upload), checked at start.

A source is read-only: neither the assistant nor any user changes it or its mirror, which only the sync writes.
A sync downloads only what changed, Nextcloud reports it cheaply and any other WebDAV server is listed in full, and it converts and indexes the files like uploads into `sources/<id>` of the data directory.
A renamed file keeps its document, while hidden files and a file sharing its name stem with another, such as `report.pdf` beside `report.docx`, are skipped.
A folder or file the server refuses to list, such as one the account may not read, does not fail the sync, which mirrors everything else, as rclone does, but deletes no file missing from that listing and names the refused paths as the source's error, while a root the server refuses or does not answer fails the sync.
Admins see each source's last sync, file count, error, progress, and held deletions under Administration, Sources, and request a sync there, which one server process starts within a minute, or right after the sync still running.
A sync that held deletions still downloads and moves everything else, and an admin confirms the deletions there after checking the source.
A source removed from the configuration loses its mirror and documents at the next start.
The source is an operator endpoint reached directly, also on a private network, so a non-null `allowedAddresses` must cover it.

A `local` source mirrors a directory the operator mounted into the server, with no `url`, `hosts`, or credentials of its own, since the operating system handles the access.
It lists every file at each sync, so use `webdav` instead for Nextcloud, which reports changes and renames cheaply.

```toml
[sources.share]
connector = "local"
options = { path = "/mnt/share" }
```

- `path`: the absolute directory, which must be an active mount point unless `require_mount = false`, so a dropped share fails the sync instead of looking like an empty one.
- `max_depth` and `max_file_size_bytes`: as for `webdav`.

Symlinks and special files are skipped and never followed, not even one a file is replaced by after the listing, and a folder or file that cannot be read is named as refused, as for `webdav`.
An SMB share on NixOS is mounted with a credentials file holding `username=` and `password=` lines, readable by root only:

```nix
fileSystems."/mnt/share" = {
  device = "//files.example.com/share";
  fsType = "cifs";
  options = [ "credentials=/run/secrets/share" "ro" "uid=hivegent" "x-systemd.automount" "noauto" ];
};
```

With Docker, a named volume mounts it and the service binds it read-only at the same path:

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

The Administration pages show the models, plugins, integrations, sources, pipelines, and global skills in effect, read-only, since all of them come from this configuration, and hold the maintenance mode and the reindex.
Their overview lists the users and groups that stored something, with their documents and conversations, since membership lives in the identity provider alone.
Admins cannot act as another user, impersonation was removed on purpose, so an admin sees only what their own token grants.

`HIVEGENT_DEBUG__ENABLE=true` adds the tool and chat debugger for admins, which do not exist otherwise.
The chat debugger opens only the admin's own conversations.

Traces are exported over OTLP/HTTP to `logfire.otlp_endpoint`, such as Grafana Tempo, and to Pydantic Logfire with the credential `hivegent.LOGFIRE__TOKEN`.
Their request spans carry no arguments of the API, such as chat bodies, and their model and tool spans no prompts, answers, or tool arguments and results, unless `logfire.include_content = true`, which also traces MCP requests and sandbox code with their payloads, and never images or other binary content.

`rate_limits.<route>` bounds each user's chat turns, answers to a turn's approvals and questions (`resume`), uploads, transcriptions, generations, and watermark detections as a token bucket of `burst` requests refilled at `per_minute`.
Each server process holds its own buckets, so behind a load balancer with several servers a user gets each limit once per server.
`jobs.max_owner_jobs` (default 20) and `jobs.max_owner_spool_bytes` (default 2 GiB) bound the unfinished upload jobs of each user and the bytes they hold spooled on disk, and a user over either gets a 429.
`rate_limits.teams` holds the same limits for the Teams bridge, whose buckets all Teams users share, so its `request` and `chat` default far higher, 200 chat turns at once and 120 per minute.

A conversation's `/tmp` is deleted after `conversations.ttl_hours` of inactivity (default 72), set with `HIVEGENT_CONVERSATIONS__TTL_HOURS`.
Changes staged for approval stay until they are answered or the conversation is deleted.

Every model-controlled web request passes through the bundled Smokescreen proxy, which rejects private and reserved addresses.
Operator-configured URLs such as a provider's `base_url` connect directly, and no request can name another endpoint.
The NixOS option `services.hivegent.allowedAddresses` additionally confines the backend to loopback and the listed addresses, so these must then be local, a unix socket, or listed, while model downloads go through the proxy.

Web search needs a metasearch service, which Hivegent does not ship, the same way it leaves PostgreSQL to you.
SearXNG is the default backend: `compose.yaml` runs its upstream image, and the NixOS option `services.hivegent.searxng.createLocally` wires in the host's `services.searx`.
OpenSERP works as well with `tools.search_backend = "openserp"`, though it searches the general web only and reports no publication dates.
Point `tools.search_url` at the service, otherwise `web_search` stays hidden, and for SearXNG list `json` under its `search.formats`.
`web_fetch` is enabled separately by `[security.web_urls] allow_hosts`, where `"*"` permits any public page the user or a search named and a list of domains confines it.
`web_fetch` refuses a URL the model writes itself, so instructions planted on a page cannot carry conversation data out in a request.
It keeps no cookies, so no page sees a session that a request of another conversation started.
The chat never loads an image from the model's answer and asks before opening a link it contains, so neither can leak data on display.

To reach an operator endpoint over a unix socket, map its host name under `[network.unix_sockets]`, for example `HIVEGENT_NETWORK__UNIX_SOCKETS='{"llmhop": "/run/llmhop/default.sock"}'`.
A provider with `base_url = "http://llmhop/v1"` then connects to the socket.
Web requests never use this mapping.
A direct client reaches only the origins of the endpoints configured for it, so it refuses a redirect or a path to any other host.
The NixOS module runs as a dynamic user, so give the socket its own static group (not `hivegent`) with mode `0660` and add it to `systemd.services.hivegent.serviceConfig.SupplementaryGroups`.

## Identity provider (OIDC)

Hivegent works with any OIDC provider, such as Rauthy, Keycloak, Authentik, or Auth0.
The browser reads the issuer and client id from the backend at runtime, so no rebuild is needed.
The Content-Security-Policy of the bundled Caddy lets the app reach no other host than the provider's origins, which you name explicitly, usually the origin of the issuer plus any further origin the provider signs in on.
The container takes them space-separated in `CADDY_IDP_ORIGINS`, the NixOS module as the list `services.hivegent.caddy.idpOrigins`.
Without an origin the sign-in would be blocked, so the container refuses to start and the NixOS module to evaluate.

The backend accepts an access token only when it is signed with an asymmetric algorithm the provider advertises (RS, PS, ES, EdDSA, or Ed25519), names the configured issuer and one of `auth.audience`, carries `exp` and `iat`, names its client in `azp` or `client_id`, and is an access token, declared by the RFC 9068 header `typ: at+jwt` or the claim `typ: Bearer` of Keycloak and Rauthy.
An ID token of the same client is refused, so a provider that marks its access tokens neither way must be set to issue RFC 9068 tokens.
The provider's discovery document must name the same issuer and serve its keys on the issuer's origin, scheme included.
The MCP endpoint verifies tokens the same way, for the audience `mcp.base_url`, spelled exactly as configured, or `mcp.client_id`.
The bundled Caddy forwards `/mcp` and the OAuth discovery documents of the endpoint below `/.well-known/`, such as `/.well-known/oauth-protected-resource/mcp`, and in `proxy` mode the identity provider must allow the redirect URI `<mcp.base_url>/auth/callback`.
The backend log names why it refused a token.

For the bundled Rauthy:

1. Read the one-time admin password with `docker compose logs rauthy`.
2. Log in at <http://auth.localhost:8081>.
3. Create a public client `hivegent-spa` with PKCE (S256), and set the redirect URI and allowed origin to `http://localhost:8080`.
4. Create users and, optionally, groups.

### Groups and roles

Access is driven by the token claims, which your provider must include in the access token.

- Every user has a private workspace and reads the global one (`/srv`), which only users with the `admin` role write.
- Groups come from the `groups` claim, or the claim paths `claims.groups` lists, such as `[["groups"], ["custom", "groups"]]` for the static claims a Rauthy client nests under `custom`.
  An entry like `engineering` grants its shared workspace, and `engineering:read` or `engineering:write` sets the permission.
- The `admin` role from the `roles` claim, or the paths `claims.roles` lists, grants administrator actions such as maintenance mode and the reindex.
  No HTTP route wipes another user's or a group's data, that runs only on the host.
  Deployment-wide wipes run on the host with `hivegent reset workspace`, `database`, or `factory`, and refuse while a server runs on the database.
  `hivegent reset user <id>` and `hivegent reset group <id>` wipe what one user or group owns, while users wipe their own data under Settings, Account.
  `hivegent export <id> <file.zip>` writes everything one user owns to a ZIP archive beside a running server, while users download their own data, all of it or one part, under Settings, Account.
  `hivegent-manage` runs any `hivegent` command exactly as the service runs, with its environment, credentials, and data, and stops the server around a `reset` and starts it again.
  In the container, it runs inside the running one:

  ```sh
  docker compose exec hivegent hivegent-manage reset group engineering
  ```

  On NixOS, it runs as root with the service's dynamic user, directories, and sandbox, through `systemd-run --pty --wait --collect`:

  ```sh
  sudo hivegent-manage reset group engineering
  ```

  `reset` refuses while the server runs on the same database, since it holds a lock in it, so stop it first.
  One server runs per database and data directory, and a second one refuses to start.

  A source removed from the configuration is dropped with its mirror at the next start, unless it holds files its source lost, whose only copy is the mirror.
  Such a source is kept, hidden from every user, and logged until it is configured again or dropped with `hivegent reset sources`.

### Group identity and renaming

A groups claim entry is either an object or a bare string.

An RFC 9068 object `{"value": "<id>", "display": "<name>"}` separates the id from the display name.
The id appears in paths (`@<id>/notes.md`), so renaming the group keeps its workspace.
Entra ID, and Keycloak, Authentik, Okta, or Auth0 with a claim mapper on the group id, work this way.

A bare string, which is all Rauthy can emit, is both name and id.
Renaming such a group leaves its documents behind under the old name.

## Scripting

The `hivegent` command-line tool only administers the server on its host, its former client commands `login`, `logout`, `upload`, `download`, `list`, `delete`, and `whoami` were removed on purpose.
Scripts use the REST API or the MCP endpoint instead, with an access token from your identity provider whose client is listed in `auth.audience`.
A token of a person, from the device flow, acts as that person, while one from the client credentials flow acts as its client's own user, whose `sub` names its private workspace.
The client of `auth.service_client_id` reaches only the chat route, so do not script with it.

```sh
ISSUER=https://auth.example.com/auth/v1
API=https://hivegent.example.com/api/v1
TOKEN_URL=$(xh $ISSUER/.well-known/openid-configuration | jq -r .token_endpoint)
TOKEN=$(xh --form POST $TOKEN_URL grant_type=client_credentials \
  client_id=hivegent-script client_secret=$CLIENT_SECRET | jq -r .access_token)

xh -A bearer -a $TOKEN GET "$API/directories/~"                                 # list
xh -A bearer -a $TOKEN --form PUT "$API/documents/~/reports/q3.pdf" file@q3.pdf # upload
xh -A bearer -a $TOKEN GET "$API/documents/original/~/reports/q3.pdf" -o q3.pdf # download
xh -A bearer -a $TOKEN DELETE "$API/documents/~/reports/q3.pdf"                 # delete
```

Paths start with `~` for the private workspace, `@<group>` for a group's, and `/srv` for the global one.
An upload answers with a job, which `GET /api/v1/jobs` follows until the document is converted and indexed.
`POST /api/v1/changes` moves, deletes, and creates several documents and directories at once, all of them or none.
With `security.expose_api_docs` the schema of every route is served at `/api/v1/docs`.

An MCP client, such as the Python packages `mcp` or `fastmcp`, connects to `mcp.base_url`, ending in `/mcp` without a trailing slash, with the same kind of token and calls the tools `list_documents`, `read_document`, `write_document`, `move_documents`, and `delete_documents`, each change asking for confirmation first.

## Updating

```bash
docker compose pull
docker compose up -d
```

Database migrations run automatically on startup.
The index follows changes of the embedding model, the full-text configurations, and the chunking options by itself at the next start, and so does an update that chunks or embeds documents differently.
Read the [upgrade notes](upgrading.md) before updating, since removed settings stop the server at startup.

## Several instances

Instances such as prod, staging, and testing are independent when each has its own database, data directory, and OIDC client, since the backend matches token audiences exactly.
One server runs per database and data directory, and a second one refuses to start.

On NixOS, `nixosModules.instances` runs each instance in a container with the unchanged Hivegent module, its own PostgreSQL, and a private network, while the host's Caddy serves every domain with TLS:

```nix
{
  imports = [ hivegent.nixosModules.instances ];

  services.hivegent.instances.staging = {
    hostName = "staging.hivegent.example.com";
    hostAddress = "10.233.2.1";
    address = "10.233.2.2";
    config.services.hivegent = {
      caddy.idpOrigins = [ "https://auth.example.com" ];
      settings = { /* as for a single instance */ };
    };
    # Credential files of the host, visible inside the container.
    container.bindMounts."/run/secrets/hivegent".hostPath = "/run/secrets/hivegent-staging";
  };
}
```

Names are at most 11 characters, the container names.
Host operations run inside the container, such as `sudo nixos-container run staging -- hivegent-manage reset sources`.
Elsewhere, run one container stack per instance, each with its own PostgreSQL and volume.

## Production notes

- Replace the example secrets, keys, and passwords.
- Serve the stack over HTTPS, through the bundled proxy with a real domain or your own reverse proxy.
- A running server holds a session-level PostgreSQL advisory lock, which `hivegent reset` checks, so a PgBouncer in front of the database must use session pooling, not transaction or statement pooling.
- A server that loses the connection of its lock stops, and its service restarts it, the NixOS unit backing off from 5 to 60 seconds and the container at least 10 seconds apart, while a stop by the operator stays stopped.
