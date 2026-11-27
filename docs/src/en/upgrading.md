# Upgrade notes

Read these notes before updating an existing deployment.
Every key of `config.toml` and every `HIVEGENT_*` variable must name a setting, so a removed or misspelled one stops the server at startup with an error naming it, instead of being silently ignored.

## Removed and renamed settings

| Before                                                                                                                                      | Now                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `llm.model`                                                                                                                                 | `llm.models.<id>.model`, chosen by `llm.default`                                                        |
| `llm.base_url`, `llm.api_key`, `llm.inference_provider`                                                                                     | `llm.providers.<id>.base_url`, `api_key`, and `inference_provider`, named by `llm.models.<id>.provider` |
| `llm.max_tokens`                                                                                                                            | `llm.models.<id>.max_tokens`                                                                            |
| `llm.aux_model`                                                                                                                             | `llm.aux`, the id of a model of the catalog                                                             |
| `llm.stt_model`                                                                                                                             | `llm.stt`, the id of a model of the catalog                                                             |
| `conversion.assets`                                                                                                                         | `conversion.extract_assets`, `false` for `ignore`, with images described whenever `llm.aux` is set      |
| `llm.subagent_timeout_seconds`                                                                                                              | `subagents.timeout_seconds`                                                                             |
| `embedding.base_url`, `embedding.api_key`                                                                                                   | `embedding.endpoint`, the id of an `llm.providers` entry                                                |
| `rerank.base_url`, `rerank.api_key`                                                                                                         | `rerank.endpoint`, the id of an `llm.providers` entry                                                   |
| `transparency.detector_url`, `transparency.detector_api_key`                                                                                | `transparency.endpoint`, the id of an `llm.providers` entry, and `transparency.detector_path`           |
| `auth.enable`, `auth.allow_disabled`                                                                                                        | removed, `hivegent serve` always authenticates and needs `auth.issuer`, `hivegent dev` never does       |
| `mcp.enable`                                                                                                                                | removed, setting `mcp.base_url` serves `/mcp`                                                           |
| `mcp.allow_unauthenticated`                                                                                                                 | removed, `/mcp` always authenticates under `hivegent serve`                                             |
| `security.user_urls`                                                                                                                        | removed, users no longer bring their own model or MCP endpoints, which only the operator configures     |
| `LOGFIRE_TOKEN`                                                                                                                             | `logfire.token`, best the credential `hivegent.LOGFIRE__TOKEN`, since the variable is no longer read    |
| `tools.disabled` default of the three conversation tools                                                                                    | `[]`, since withholding them now also switches off the `conversations` subagent                         |
| `HIVEGENT_SITE_ADDRESS` (container)                                                                                                         | `CADDY_SITE_ADDRESS`                                                                                    |
| `enableMcp` (`docker.override`)                                                                                                             | removed, Caddy always forwards `/mcp`, which the backend serves only with `mcp.base_url` set            |
| `claims.groups`, `claims.roles` default of `custom.*`                                                                                       | `[["groups"]]` and `[["roles"]]`, paths are lists of keys such as `["custom", "groups"]`                |
| `network.contact_email` fallback to the package author                                                                                      | none, an unset address sends no contact in the `User-Agent`                                             |
| `hivegent.LLM__API_KEY`, `hivegent.EMBEDDING__API_KEY`, `hivegent.RERANK__API_KEY`, `hivegent.TRANSPARENCY__DETECTOR_API_KEY` (credentials) | `hivegent.LLM__PROVIDERS__<ID>__API_KEY` of the provider they use                                       |

The new `CADDY_IDP_ORIGINS` of the container, space-separated, and `services.hivegent.caddy.idpOrigins` of the NixOS module, a list, name the origins of the identity provider for the Content-Security-Policy of the app.
They are required, the container refuses to start and the NixOS module to evaluate without one, since the policy would otherwise block the sign-in.
A `HIVEGENT_CONFIG_FILE` naming no file now stops the boot, and the container names `/data/config.toml`, so mount one.
`docsPath` must start with a slash and end without one, a trailing slash is refused instead of dropped.
Settings that took an empty string for unset now take no value instead: `auth.issuer`, `mcp.base_url`, `mcp.client_id`, `network.contact_email`, and `transparency.contact_email`.
An empty `auth.issuer`, `mcp.base_url`, `mcp.client_id`, `network.contact_email`, or `transparency.contact_email` stops the boot, as does an operator URL that is none, such as an `auth.issuer` without a scheme.
A Qwen3.8 model no longer sends `xhigh` for the effort `high` by its name, set `reasoning_overrides = { high = "xhigh" }` on it instead.
Exported traces no longer carry message content, set `logfire.include_content = true` to keep prompts, answers, and tool arguments and results in them, while images and other binary content are no longer exported at all.
The request spans of the API no longer carry its arguments, such as chat bodies, unless it is set, and keep only the type and location of a validation error.
The spans of MCP and of the Monty sandbox are only recorded with it, since they hold whole payloads and the executed code.
`mcp.base_url` and `transparency.report_issuer` must be URLs, `llm.default`, `llm.aux`, and `llm.stt` catalog ids, and `logging.level` one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` in capitals.

## Embedding and reranking

`embedding` and `rerank` each take the fields of their `provider` only, and the boot refuses the others.

- `embedding.text_search_config` is a list, so a single configuration such as `"simple"` becomes `["simple"]`.
- A remote embedder (`provider = "openai"`) needs `endpoint`, `model`, and `dimension`, while the local one keeps its defaults.
- Reranking is off while the `rerank` section is left out, and a `rerank` section needs `provider` and `model`.
  `rerank.top_n` keeps the best rescored candidates of either provider.
- A remote embedder cuts over-long texts to the limit the model reports, which only OpenAI's own models do.
  Set `embedding.request.extra_body.truncate_prompt_tokens = -1` for vLLM, since another server must truncate them itself.
  It now sends `embedding.batch_size` texts per request (default 128), `embedding.concurrency` requests at a time (default 4).
- `embedding.query_prefix` and `embedding.document_prefix` prepend a prefix or instruction to every query and chunk, which models such as e5 and Qwen3-Embedding expect.
  Changing `embedding.document_prefix`, like the model, re-embeds every chunk at the next start.

## Token validation

The REST API, the job and chat streams, and `/mcp` now share one verifier, which is stricter than before.

- `auth.issuer` must be an `https` URL, unless its host is `localhost`, a subdomain of it, `127.0.0.1`, or `::1`.
- A token must carry `exp` and `iat`, and its expiry is granted 60 seconds of clock skew instead of five minutes.
- A token must be an access token, declared by the RFC 9068 header `typ: at+jwt` or, as Keycloak and Rauthy issue them, by the claim `typ: Bearer`, so an ID token of the same client is refused.
- A token must name its client in `azp` or `client_id`, and the two must agree when both are present, so a token of the bridge never passes as a person's.
- The cached signing keys stay trusted while the issuer is unreachable for at most `auth.jwks_max_age_seconds`, six hours by default, after their last successful fetch.
- It must be signed with an asymmetric algorithm, RS256, RS384, RS512, PS256, PS384, PS512, ES256, ES384, ES512, EdDSA, or Ed25519, and only those of them the provider advertises in its discovery document are accepted.
  Tokens signed with `none` or an HMAC algorithm (HS256, HS384, HS512) are refused.
- The `issuer` of the discovery document must equal `auth.issuer`, up to a trailing slash, and the `iss` of every token must equal the discovery document's `issuer` byte for byte.
- The `jwks_uri` of the discovery document must lie on the origin of the issuer, scheme included, so an `https` issuer serves its keys over `https`.
- `auth.audience` lists exact client IDs, is required, and refuses wildcards, so replace a pattern such as `hivegent-*` with every client ID it matched.
- A token for `/mcp` must name `mcp.base_url`, spelled exactly as configured, or `mcp.client_id` when it is set, as its audience.
  The reason a token is refused is logged by the server.

Long-lived streams, the job feed and following a running chat turn, now end when their token expires, and the app reconnects with a fresh one, so a removed group membership or admin role takes effect then.
A running chat turn and a queued document job act with the grants of the token that started them, which every later request of the same user renews, as the open app does through its streams.
A token no longer holding their grants, such as after a group was taken away, renews neither, so a turn stops a minute after the latest renewing token expired, storing what it got to, and a queued job then fails without starting.

## MCP endpoint

`mcp.base_url` must be the endpoint's public URL, whose path is exactly `/mcp`, such as `https://hivegent.example.com/mcp`, which the boot checks.
The endpoint serves stateless HTTP, without sessions and without a GET stream.
It now answers at `/mcp` alone, no longer at `/mcp/`, so remove a trailing slash from the URL of every MCP client.
It advertises `mcp.base_url` as its resource exactly as configured and serves its OAuth discovery documents at the root, such as `/.well-known/oauth-protected-resource/mcp`, which the bundled Caddy forwards, so a reverse proxy of your own in front of the backend must forward them too.
The callback of the `proxy` mode stays `<mcp.base_url>/auth/callback`.

## Rate limits and outbound requests

Every authenticated request, to the REST API and to `/mcp`, now spends a token of the new `rate_limits.request` bucket, and maintenance mode now also locks non-admins out of `/mcp`.
Reconversions, rechunks, document writes, and workspace changes now spend the `upload` bucket, and case base queries and conversation archives the `generation` bucket.
A chat turn spends the `chat` bucket, while answering the approvals or questions a turn awaits spends the larger `resume` bucket instead, since that only continues the turn.
The buckets live in the server's memory, and a bucket that refilled is forgotten.
`jobs.max_owner_jobs` (default 20) and `jobs.max_owner_spool_bytes` (default 2 GiB) bound the unfinished upload jobs of one user and the bytes they hold spooled on disk, and a user over either gets a 429.
Deleting a user cancels their jobs first.

Operator endpoints are now reached by a direct client confined to their own origin.
An external MCP server with OAuth client credentials reaches its own origin, the origin of `oauth.issuer`, and the origins of the endpoints the issuer's own discovery metadata names, such as its token endpoint.
A plugin's `host.http_client()` is now the web client, which reaches only the public hosts `security.web_urls` allows, through the egress proxy.

## Teams bridge

The bridge no longer runs without `auth.service_client_id`.
Its `backendUrl` must be `https`, unless it names a loopback host.
At startup it checks that its token authenticates as the service principal and refuses to start otherwise, since all Teams users would else share one ordinary account with its memory and conversations.
It also needs an allowlist of the Microsoft Teams tenants it serves and refuses activities from any other tenant.

The bridge now reads all its settings from one strictly validated JSON file, `BRIDGE_CONFIG_FILE` (default `config.json`), and only its secrets from the environment or its credentials: `POSTGRES_URL`, `OIDC_CLIENT_SECRET`, and `TEAMS_APP_PASSWORD`.
The bot is now a read-only service principal that sees the global workspace and the sources without `groups`, no longer the group workspaces its groups claim granted.
Move a knowledge base it answered from, such as `@team-kb`, into the global workspace, or declare it as a source without `groups`, before upgrading, since its Teams users otherwise search nothing.

`HIVEGENT_URL` became `backendUrl`, `ALLOWED_TENANTS`, `TEAMS_APP_ID`, and `TEAMS_APP_TENANT_ID` became `teams.allowedTenants`, `teams.appId`, and `teams.tenantId`, and `HOST`, `PORT`, `BOT_USERNAME`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_SCOPE`, and every `ENABLE_*` became the keys of the file, with `adapters.teams` now required.
`REASONING_EFFORT` is gone, since the bot runs with the reasoning effort hivegent keeps for its service principal, the server default.
At startup the bridge now retries a network error, timeout, 5xx, or 429 of its token request to the identity provider or of `GET /api/v1/principal` for up to 5 minutes, backing off from 1 to 30 seconds, while any other failure, such as a token that is no service principal, still stops it at once.
On one NixOS host, its unit now starts after `hivegent.service` and wants it, and in the container a failed bridge no longer stops the container.
A leftover `HIVEGENT_URL` in an environment the backend shares, such as one container environment for both, now stops the backend, since it names no setting.

## Command-line client

The client commands of the `hivegent` command-line tool, `login`, `logout`, `upload`, `download`, `list`, `delete`, and `whoami`, were removed on purpose.
Delete the `~/.config/hivegent/credentials.json` that `login` left behind, since it holds the client secret in plain text.
`hivegent serve --allow-unsafe-auth-disabled-bind` is gone too, since `hivegent serve` always authenticates.
Script the same through the REST API or the MCP endpoint, as [Scripting](setup.md#scripting) shows.
Wiping another user's or a group's data, or the whole deployment's, runs only on the host with `hivegent reset`.
Admins can no longer act as another user, since the `X-Impersonate-User` header is gone.
The tool and chat debug consoles are off by default, set `debug.enable = true` to offer them to admins again, and the chat debugger opens only their own conversations.

## Document conversion

- Pipeline options are the operator's alone, set per pipeline id in `conversion.pipelines` and `chunking.pipelines`, so an upload names no pipeline, a request sending `config` fails with 422, and the settings page no longer edits them.
- The `marker` and `mineru` converters and the `fast`, `code`, `neural`, and `late` chunkers are gone, and a document that recorded one keeps its chunks until it changes or is rechunked, which then uses the default.
- The chunker options `chunk_size` and `chunk_overlap` are now `size` and `overlap`.
- `.dot`, `.pot`, and `.xlt` files are read as plain text instead of converted as Office templates.
- An uploaded folder or ZIP with a single top-level folder keeps that folder instead of being unwrapped.
- `compute.device` places the models of docling, the local embedding model, and the local reranker, `cpu` by default instead of a detected accelerator, and `mps` is the only alternative the docs support, for development on macOS.
- Linux builds are CPU-only: the CUDA build of torch is gone, the NixOS module no longer lifts `PrivateDevices` for the NVIDIA devices, and the commented `nvidia` block of `compose.yaml` is removed, so drop a GPU reservation of your own.
- Conversions and the `semantic` chunker run in worker processes, while the other chunkers, which load no model, run in a thread of the server, so they never queue behind conversions.
- A task of a worker now ends after `compute.task_timeout_seconds` (default 1800), and its worker is replaced, so a hanging converter no longer blocks the pool, and a document that times out falls back to plain text extraction.
  A worker is also replaced after `compute.worker_max_tasks` tasks (default 200) against leaks, which reloads its models, and either is `null` for no limit.
- `pandoc` and `pdftotext` are stopped past 4 GiB of memory, `pandoc` also past 256 MiB of images extracted from one file, its `max_assets`, which is also its limit for writing one file, `pdftotext` past 256 MiB of output, and both see only `PATH`, `LANG`, and `LC_ALL` of the environment.
- The OCR cache of Kreuzberg is disabled, and `XDG_CACHE_HOME` is `/var/cache/hivegent` on NixOS and in the container.
- Still images read by the View tool and attached to the chat are downscaled to 1024 px.
- Every temporary file lies below `<data_dir>/.transient`, the temporary directory of the server and its workers, which the boot empties.
- The `markdown` chunker splits a passage, table, or code block longer than its `size` (default 2048) and keeps the images of a passage, so a document chunked with it chunks differently once it changes or is rechunked.
- Every document converted from now on records the converter and options it was made with, while those converted before record none.

## Conversation store

The first boot rewrites every message id of the database, since the conversation store mints its own, and the migration cannot be reversed.
Back up the database and the data directory together before starting the new version, the only way back is the backup, and restore them together, since the files on disk and the rows describe each other.
Conversations keep their ids, so links keep working.
Changes staged for approval and still pending across the upgrade are dropped and answer as expired, so ask for them again.
Requests now store their system prompt only where it differs from the one before them in their conversation, which roughly halves the storage of conversations.
Its migration rewrites every request row.
These one-time rewrites leave `messages` about twice its size until vacuumed.
Run `VACUUM (ANALYZE) messages` once after the first boot to make the space reusable, which keeps conversations available.
`VACUUM FULL messages` or pg_repack returns it to the system, but the first locks conversations while it runs, so plan downtime for it.
A `.env` file is no longer read, so move its values into the environment, credentials, or `config.toml`.

## Background subagents

- Subagents run in the background by default and outlive the turn that started them, so you can keep chatting while they work.
  The assistant may wait for a short task whose result it needs, which moves to the background if it still runs after 30 seconds.
  Their reports reach the assistant at its next step, or, when no answer is running, the conversation answers them on its own in a new reply.
- A message you send while such a reply runs stops it, and your turn reads the reports instead.
- The new Tasks tab beside the chat lists the delegated tasks with everything their subagents did, and stops a running one.
- Tasks are saved in a `tasks` folder below each conversation's folder in the data directory and deleted with the conversation.
  A task still running when the server stops loads as stopped.
- The steps of subagents from earlier releases no longer show under their delegation in the chat.

## Prompt stability

- The system prompt no longer holds the date, it is a note appended like the mode note, so the prompt prefix of a conversation, and its provider cache, stay valid across midnight.
  Changing the mode, tools, model, or language between turns still changes it, and so does an outage of an external MCP server, which changes the tool list.
- A subagent past its report deadline keeps its tools and instructions, and its further tool calls are refused with a result telling it to report now.
- A subagent's final report is cut to `llm.tool_output_max_chars` with the whole text saved under `/tmp/.tool-results/`, as for any tool result.
- Skill instructions, however loaded, are cut to `llm.tool_output_max_chars` and point at the rest in `SKILL.md`.
- An imported archive is held to `multimodal.max_images` images per message and `llm.tool_output_max_chars` per tool result.
- The approval summary of a changeset is stored once, on the response that asked, and every saved turn or compaction keeps only the changesets that a pending `apply_changes` call still awaits.

## Database pooling and restarts

A running server holds a session-level PostgreSQL advisory lock, so a PgBouncer in front of the database must use session pooling.
The new `db.pool_size` (default 10), `db.max_overflow` (default 10), and `db.pool_timeout_seconds` (default 30) size the connection pool of each server, whose locks take connections of their own outside it, so make sure `max_connections` of PostgreSQL covers `pool_size + max_overflow` plus about 2 for every server.

A server that lost the connection of its lock now stops, and its service restarts it.
The NixOS unit restarts always, backing off from 5 to 60 seconds, at most 20 times in 10 minutes, and the container restarts the backend at least 10 seconds apart, at most 60 times in 10 minutes.
A stop by the operator, such as `systemctl stop`, `dinitctl stop`, or one for `hivegent-manage`, is not restarted.

## Self-hosted runtimes, cost limits, and MCP key

- `inference_provider = "openai"` now means OpenAI's own API, so set `sglang`, `ollama`, or `openai-compatible` on any other server that used it, which keeps its models from being priced and tokenized as OpenAI ones and, on SGLang and Ollama, enables reasoning control.
- A self-hosted provider now carries its runtime's name, such as `llama.cpp`, in traces and stored responses, where it read `openai`.
- A model on a `llama.cpp` provider no longer has its family guessed from its served name, so set `llm.models.<id>.family`, such as `qwen`, to keep its tool and schema handling.
- `family` is refused on a `vllm`, `ollama`, or `openai` provider, which tell the family from the served name, and a `llama.cpp` model named `<vendor>/<model>`, such as `Qwen/Qwen3-32B`, takes its vendor's family without one.
- `subagents.max_cost` counts only models pydantic-ai can price, so a self-hosted model is bounded by request and token limits.
- `sandbox.execution_clock` defaults to `cpu`, which Monty measures on Linux alone, so a server on another system sets it to `wall`.
- Serving `/mcp` needs the secret `hivegent.MCP__CONFIRMATION_KEY` of at least 32 characters, which signs the approvals of its clients so they hold across restarts, and the server refuses to start without it.

## Agent tools

- Subagents are delegated to through `delegate_task` and its task tools instead of the `explore` tool, so a `tools.disabled` entry naming `explore` as a tool now withholds the `explore` subagent.
- The MCP endpoint no longer serves the `explore` subagent tool, an MCP client calls the explore tools themselves.
- A write that is no workspace change, such as `write_memory`, a plugin tool, or an external MCP tool, now asks for approval in the `write` mode too.
- Search results no longer carry a `score`, since it only ranked them, and their order is the ranking.
- The conversation tools, `list_conversations`, `get_conversation`, and `search_conversation`, are offered by default, so list them in `tools.disabled` to keep past conversations out of reach.

## Document filter entries

A user message names the documents it is about in its metadata, `{"documents": {"included": [...], "excluded": [...]}}`, instead of the chat request's `included_documents` and `excluded_documents`, and every entry is an object of a path and a `directory` flag, such as `{"path": "~/handbook", "directory": true}`, instead of a string whose trailing slash marked a directory.

## Removed features

Users can no longer choose a personality, write a custom system message, add their own MCP servers, or bring their own model, endpoint, and key, so their saved servers are dropped.
The app turns a custom system message that a browser kept into the user's instructions (Settings, Instructions) once that user signs in there, unless they already wrote some.
The operator's model catalog and MCP catalog take their place, and the operator's own instructions are `llm.instructions`.
There are no assistants, neither the operator's, a group's, nor a user's.
The frontend feature flags are gone too, and with them the hidden pickers of the conversion and chunking pipelines and the asset mode, so every upload runs the operator's pipelines, and `conversion.extract_assets` sets whether every conversion extracts the images of a document.
Users can no longer switch off tools themselves, only `tools.disabled` withholds them.

## Preferences are kept on the server

The mode, chat model, reasoning effort, and instructions a user chooses are kept by the server (`GET /preferences`, `PATCH /preferences`), so they follow the user across browsers and devices and apply to every client, the Teams bot included, whose service principal keeps its own.
A chat request is the AI SDK's body alone, plus `compact`, and is refused with a 422 when it sends any other field, such as `assistant`, `mode`, `model`, `reasoning_effort`, or `disabled_tools`.
The browser keeps only the expanded folders, so each user's earlier choices are reset once.

## REST API

The routes changed under the same `/api/v1` prefix.

- `DELETE /documents/{scope}` is now `DELETE /workspaces/{scope}`.
- `POST /mcp/test` and `GET /pipelines/{stage}/{pipeline}/config` are gone, since only the operator configures MCP servers and pipelines, and `GET /pipelines/{stage}` is now `GET /admin/pipelines/{stage}`.
- The admin routes wiping a user's, a group's, or all data are gone, see [Command-line client](#command-line-client).
- Uploads, collections, rechunks, reconverts, and document writes no longer take a pipeline (`pipeline_spec`, `pipeline`, `chunking`), since every document is processed with the operator's pipelines, while each source keeps its own.
- `POST /changes` and the single-item move, delete, and directory routes answer the applied change with a new `id`, under which the job feed announces it to the caller too, since the `X-Client-Id` header is no longer read.
  A client that applied the answer skips the announcement with the same `id`.
