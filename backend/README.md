# Hivegent Backend

The invariants this service is built on and the reasoning behind them.
The system shape is in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), the client half in [`../frontend/README.md`](../frontend/README.md).

`uv run pytest` is the whole test suite and needs no services, since every test is stateless and stubs the paths that would hit PostgreSQL with `monkeypatch`.
Migrations, the running server, and retrieval are covered by the dev stack and manual smoke tests.

## The REST API

### One versioned contract

Every route lives under `config.API_PREFIX` (`/api/v1`), and a breaking change bumps the version rather than adding a compatibility layer.
The OpenAPI document, `/api/v1/openapi.json` with `/api/v1/docs` beside it, is served while `security.expose_api_docs` is set, the one switch that decides, since the reverse proxy hands the backend every `/api/*` path.
Every body is a typed model, the chat turn's too (`types.ChatRequest`), so the document describes what a client sends and a misspelled field is refused rather than ignored.

### Every error is a problem

Every error answers `application/problem+json` (RFC 9457, `server.problems`), whichever layer raised it: an `HTTPException`, a `ChangeRefused` of the changeset gateway, a validation failure, an `AccessDenied`, or an unhandled error as a bare 500.
`title` is the status phrase and `detail` the localized explanation a client shows, and a rejected body lists its invalid members under `errors`, each by a JSON Pointer such as `#/messages/0`.
A refusal a client has to tell apart from its status names a problem `type` of its own, a `tag:` URI such as `server.maintenance.MAINTENANCE_PROBLEM`.

### Methods say what a retry does

A request a client may repeat uses a method whose repetition lands the same state, so a client retries it after a lost answer without asking what happened.
`PUT /documents/{path}` creates or replaces, and `If-None-Match: *` asks for a new document only, refused with 412 while one exists.
`PUT /directories/{path}` creates a directory and leaves one that exists, `DELETE /directories/{path}`, `DELETE /workspaces/{scope}`, and `DELETE /user-data` remove what they name, and `PUT /conversations/{id}/archive` restores an export at an id the client minted, replacing what the caller held there.
A move, a generation, and the batch of `POST /changes` change something different each time, so they stay `POST`, and a chat turn is keyed by the message id the client minted (see "Clients choose conversation and message ids").

### Every request is admitted, expensive routes are rate-limited per user

Every authenticated request, to the REST API and to `/mcp` alike, passes `server.rate_limit.admit`: the maintenance gate and the `rate_limits.request` bucket, as a router dependency of the API and as ASGI middleware behind FastMCP's authentication on `/mcp`.
On top, each costly route declares the bucket it spends once, with its dependency: uploads, reconversions, rechunks, writes, and workspace changes (`upload`), transcription, generations, case base queries, and archives, and watermark detection, refused with 429 and `Retry-After` once empty.
The chat route spends its bucket itself, since only the parsed request and the stored turn tell which: a request answering the approvals or questions a turn awaits spends `resume`, which continues the turn rather than start one, and anything else, a continuation answering nothing included, spends `chat`, so an approval-heavy session does not run out of turns.
A bulk request names at most `server.models.MAX_CHANGE_OPERATIONS` paths.
Each upload route bounds its body by FastAPI's `max_body_size` to its file limit plus the form around it (`operations.upload_body_limit`), so a larger one is refused with 413 while it arrives rather than once it is spooled, and a form parses at most one file and two fields (`form_limits`).
The Teams bridge spends its own `rate_limits.teams` buckets instead, since all Teams users share its one identity, and its users stay unnamed, since the backend could not verify who they are.
The buckets live in memory, which is exact because `hivegent serve` runs one uvicorn process, and the backend rather than Caddy holds them, since only it knows the user.
Several processes would each grant the full limit, so scaling out means moving the buckets into PostgreSQL, not running more workers.

### One verifier for every token

`auth.authenticate` verifies every bearer token, of the REST API, its streams, and `/mcp` (`mcp.app.HivegentVerifier`), the latter for its own audience, `mcp.base_url` or `mcp.client_id`.
It accepts only asymmetric signatures (`auth.ASYMMETRIC_ALGORITHMS`, intersected with what discovery advertises), so neither `none` nor a shared secret passes whatever the provider advertises, and requires `iss`, `sub`, `aud`, `exp`, and `iat`.
The discovery document must name the configured issuer, and its `jwks_uri` must lie on the issuer's origin, scheme included, so an https issuer's keys come over https.
A refused token answers 401 naming at most the claim, while the log names the reason.
A stream (`/jobs/events`, `/conversations/{id}/stream`) ends when the token that opened it expires (`auth.until_expiry`), so its client reconnects with a fresh one and the grants are read again.

## Interface language

The frontend sends `Accept-Language`, and `LanguageMiddleware` makes it the ambient language of the whole request, streamed body and background tasks included.
Every text a user reads is a `Localized` value from `shared/l10n.py` with one field per language, so a missing translation is a type error, resolved with `.current` where it is raised.
Fixed text is an UPPER_CASE constant and text with parameters a snake_case function returning `Localized[str]`, both named after the condition they report (`_host_blocked`) rather than their wording.
Fixed option values such as pipelines are sent as enum values and described by the frontend catalog.

The agent's instructions and injected notes are composed in the run's language (`UserDeps.language`) and end with a pin to answer in it.
Tool names, descriptions, schemas, results, and retries stay English, since models call tools most reliably from English schemas, and `EnglishToolCalls` runs every tool call in English.
A query argument says which language to phrase the query in, since copying the user's language into a search over documents in another language is the common failure.
Features hold one capability per language around the same toolset rather than an instruction callable, which pydantic-ai would treat as dynamic and move behind the cache boundary.
State that changes during a conversation is an instruction that appends its changes (see below).

## Database

### Writes are atomic

Repository functions create or update rows atomically, never as a read-then-write, since the loser of that race rolls back its whole transaction on `IntegrityError`.
Single rows use `INSERT ... ON CONFLICT` (`ensure_row` in `db/_common.py`, `.on_conflict_do_update` in `upsert_document`), and a core upsert bumps `updated_at` in `set_` with `func.now()`, since it does not fire the ORM `onupdate`.
Multi-row sequences no constraint can guard are checked by a revision, as the conversation store's save checks its record's, or take a transaction-scoped advisory lock.
The primary keys, unique constraints, and `workspaces.parent` stay as the safety net.
The one accepted exception is a best-effort last-write-wins update with no constraint to violate, such as a throttled activity timestamp.

### Schema and migrations

Alembic owns the schema, and the migration tree lives in the package at `src/hivegent/migrations/`, so revisions ship with the wheel.
Chunk metadata, text, and vectors live in the `chunks` table and cascade with their document, so there is no separate vector index.
The FastAPI lifespan runs `apply_migrations()` before accepting traffic, under a transaction-scoped advisory lock so servers booting together migrate one after the other, then `verify_schema()` refuses to serve a database whose schema differs from the models as a new revision would find it, and `reconcile_index_state()` hands the index to cbrkit's `Pgvector.reconcile`.
That fits `chunks.embedding` to `settings.embedding.dimension` and `chunks.tsv` to `settings.embedding.text_search_config`, and re-embeds every chunk when the fingerprint of the vectors, the embedding provider, model, and document prefix, differs from the one it keeps as the comment of `chunks.embedding`.
Those two columns and their indexes follow the settings rather than a revision, so the comparison and autogenerate leave them out (`db.migrations.include_object`).

Revisions are written by hand, since `--autogenerate` needs a live PostgreSQL with `pgvector`.
Every change to `db/models.py` gets a matching revision under `migrations/versions/` in the same commit, named `YYYYMMDD_HHMM_<revision>_<slug>.py`, with `down_revision` set to the current head, symmetric `upgrade()` and `downgrade()`, and constraint names as the naming convention on `Base.metadata` produces them (`pk_users`, `fk_documents_workspace_key_workspaces`), wrapped in `op.f(...)`.
Verify against the dev database, started by the dev shell or `nix run .#hivegent`:

```sh
uv run alembic upgrade head
uv run alembic check
```

An update of exar or cbrkit that chunks or embeds otherwise ships a revision that clears the stamps of every document, which the fingerprints of the index cannot notice, since they cover options and not code:

```python
def upgrade() -> None:
    # exar 1.2 splits sentences otherwise, so every document is chunked again.
    op.execute("UPDATE documents SET content_digest = NULL, chunker = NULL")
```

The boot migrates before it reconciles the index, so the next start chunks and embeds every document again, and operators run nothing.

`hivegent migrate [<revision>]` and the standard `alembic` subcommands work for ad-hoc use.
In production the systemd unit from [`nixosModules.default`](../nix/nixos/service.nix) migrates during startup, and a failed migration exits non-zero, which the unit restarts (`Restart = "always"`).

### cbrkit and exar come from their dev branches for now

`[tool.uv.sources]` points cbrkit and exar at the `dev` branches of their repositories, as Hivegent builds on cbrkit 2 and exar before they are released, and the commented entries beside them at editable checkouts for working on all three at once.
The source entries go once the releases are out.
The dependency carries no version bound meanwhile, since the checkout still reports version 1.7.1, and becomes `>=2,<3` with the cbrkit 2.0 release.

## Configuration

### Secrets are credential files

Settings layer init arguments over environment variables, credential files, and the TOML file.
No `.env` file is read, since the working directory is the writable data directory in production.
A credential is a file in `$CREDENTIALS_DIRECTORY`, which systemd sets for the unit and the container image points at `/run/secrets`.
It is named `hivegent.<NAME>` for the variable `HIVEGENT_<NAME>` it replaces, e.g. `hivegent.LLM__PROVIDERS__LOCAL__API_KEY`, so the bridge's `hivegent-bridge.*` credentials stay apart in a shared directory.
On NixOS the unit also imports every `hivegent.*` credential from the system credential store, see `services.hivegent.credentials`.
Unlike an environment file, a credential is readable only by the unit's user and stays out of every subprocess's environment.
Every secret setting is a `SecretStr`, unwrapped by `config.reveal` only where a client needs the value.

### A model without vision is sent no image

A conversation may switch models between turns, so a model without `vision` may inherit a history holding images, attached or read on another.
`agents.guards.VISIONLESS_IMAGES` (`exar.pydantic_ai.VisionlessImages` with the vision of the run's LLM config) sends it every image as a short note on the wire only, so the stored conversation keeps them for a later model that can see, and a new attachment to it is refused outright.
It is offered no tool flagged `seeing` either (`read_binary_document`, `AgentTools.seeing`), which `agents.guards.OFFERED_TOOLS` withholds on every agent, since what such a tool returns would only reach it as notes.

### Operator endpoints are typed

Every endpoint the operator configures is an `security.OperatorUrl`, which only the settings mint, as an AST test holds (`test_egress`), and a direct client (`http_client.get_trusted_http_client`) is opened only for such URLs and refuses every request to another origin, so neither a library nor a plugin reaches a host the operator did not name over it.
The same test keeps every HTTPX client in `http_client` and `security`, so none honours proxy variables or reaches a unix socket by accident, and a plugin gets the policy-checked web client.

### Unknown settings stop the boot

Every settings section refuses a key it does not declare, in the TOML file and in `HIVEGENT_*` variables alike, so a removed or misspelled setting stops the boot naming it instead of leaving the default in its place.

### Endpoints are declared once

Every OpenAI-compatible endpoint is one `llm.providers` entry with its key, and a remote embedder or reranker names it as `embedding.endpoint` or `rerank.endpoint`, so a key lives in one credential, and the boot refuses a remote one naming none or an unknown one.
`embedding` and `rerank` are discriminated unions keyed on `provider`, a local variant (`sentence-transformers`) without an endpoint and a remote one (`openai`, `http`) that requires it, so each variant holds only the fields that apply to it, and an unset `rerank` disables reranking.
The watermark detector speaks another contract at a path of its own, so it keeps `transparency.detector_url` and its key.
Every consumer of a provider, from a chat model to the detector, takes the whole `LlmProviderSettings` rather than its fields one by one.

### A request names, the operator configures

A request picks a chat model by its catalog id and nothing else, so no browser can point a call at another endpoint or spend the operator's key there.
`llm.providers` holds the endpoints with their keys and `llm.models` the models they serve, both keyed by id so a credential file addresses a provider's key, and the roles `default`, `aux`, and `stt` name models by id, all checked at boot.
`llm_config` resolves a model with its provider into an `LlmConfig`, whose key stays a `SecretStr` until `llm.create_openai_provider` reveals it.
An unknown or non-chat id is a `422`, a reasoning effort the model does not list is clamped to the nearest one it does (`clamp_reasoning`), none turning thinking off, and an image for a model without `vision` is refused.
`GET /settings` lists the chat models with their label, vision, reasoning efforts, and context window, never a URL or whether a key is set.
Everything that answers the user runs on the turn's chat model, which keeps the cached prefix: the main agent, its subagents (their agents carry the turn's settings, since the harness does not pass run-level ones on), compaction, the case base prompts, and a program's `complete`.
The main agent, its subagents, and the case base prompts reason with the turn's effort, compaction never reasons, since a summary gains little from it and thinking could spend its whole cap, and a program's `complete` reasons only at the effort the program asks for.
A cap meant for an answer (`thinking_model_settings(config, answer_tokens)`) gets the thinking budget of the call's effort on top where the runtime enforces one (llama.cpp and vLLM), since it counts reasoning against the completion cap.
The aux model serves only one-off utility work with thinking always off: conversion, captions, titles, and LLM-guided chunking, all through `llm.complete`.
A model without `reasoning` takes any requested effort unchanged and `GET /settings` offers all levels for it, while an empty list turns thinking off and a list clamps to its levels.

### A provider is named for its runtime

`llm.create_openai_provider` gives OpenAI's own API pydantic-ai's `OpenAIProvider`, vLLM and Ollama their own providers, and llama.cpp, SGLang, and any other OpenAI-compatible server an `OpenAICompatibleProvider` named for the runtime, so every response, span, and embedding model carries the runtime's name.
A self-hosted model is thus never priced as OpenAI's namesake, nor has its embedding input counted with OpenAI's tokenizer and cut to OpenAI's 8192 tokens, which is why only OpenAI's own models are cut client-side and a self-hosted server truncates for itself, vLLM with `truncate_prompt_tokens`.
The provider profiles the model by its served name, the vLLM and Ollama ones by the families they serve and the OpenAI-compatible one by a `<vendor>/` prefix, and `llm.create_openai_chat_model` layers the configured `family` on top where the runtime takes one (`config.FAMILY_PROVIDERS`), the boot refusing it elsewhere, then the self-hosted profile, and the configured `context_window`, which pydantic-ai and pydantic-ai-harness otherwise know only for the models of genai-prices.
Thinking goes out as pydantic-ai's unified `thinking`, and llama.cpp, vLLM, and SGLang also get `chat_template_kwargs.enable_thinking`, with the budget of `llm.thinking_budget_tokens` on the two that cap it per request.

### Strict tools require every argument on vLLM

vLLM enforces a tool sent with `strict` through an xgrammar grammar that takes the properties in their declared order.
With optional properties, a model that writes a later one first can no longer write any declared before it, which left `similar_cases` with only `limit` of `path`, `query`, and `limit`, every time and even at temperature zero.
`llm.StrictJsonSchemaTransformer` therefore requires every property of a strict schema on vLLM, an optional one as nullable, so each argument has its place and the model writes null for the ones it leaves out.
pydantic-ai's `OpenAIJsonSchemaTransformer` does not fit, since it drops `strict` from every tool with an optional argument, or, forced to strict, closes a free-form object such as a query record.

### Cost counts priced models only

pydantic-ai prices each response with genai-prices, by the endpoint it came from before the provider's name, and adds up the known costs, so a model it cannot price adds nothing and `subagents.max_cost` bounds the priced usage of a turn alone.
A self-hosted model has no per-token price, so a cost limit means nothing for it and `llm.request_limit` and the token limits bound it instead.
exar's `Limits.max_cost` counts the same way.

A request picks a converter or chunker by its registered id and nothing else, since `ConversionSpec` and `ChunkingSpec` refuse any other field and any id the registry lacks.
Their options come from `conversion.pipelines` and `chunking.pipelines`, keyed by pipeline id, the fields of the pipeline's `exar.ingest` parser or chunker such as `{"recursive": {"size": 1024}}`, and validated at boot (`pipelines.check_pipelines` from the lifespan, as `config` cannot import the pipelines that import it), unknown fields refused, so a typo stops the server and no browser reaches the model paths of a backend.
`conversion.assets` is how every conversion handles the assets it extracts, uploads and sources alike: `ignore` drops them with their references, `store` (the default) keeps them beside the document, and `describe` also describes the meaningful ones.

### Pipelines register by id

The parsers, chunkers, media decoders, and worker executors are exar's (`exar.ingest`), and Hivegent keeps only the glue: the registries, the settings binding, the `Localized` labels, and the plugin registration.
`pipelines.CONVERTERS` and `pipelines.CHUNKERS` map an id to a `shared.pipeline_registry.Pipeline`, the built-in ones wrapping exar's parsers and chunkers, the app's own (`llm` and `slumber`, which call the operator's models), and every enabled plugin's, refusing a taken or malformed id, so the id a document records names exactly one pipeline.
A registration is static metadata and exar imports each backend on its first call, so listing and AUTO routing import none, and AUTO tries the converters declaring a suffix in registration order, wrapping docling in a host-side fallback with anydoc, each a task of its own so a crash, timeout, or error of the first still gives the second a worker, for Office files it refuses and `pdftotext` for PDFs it garbles, where a fallback that yields no text fails too, so docling's error stands and the file becomes a stub with its name, type, and size, and a recovery is logged as a warning.
Every pipeline is built by `exar.ingest.configure` from the facts of the `exar.ingest.Host` it has a field for (`pipelines.HOST`: threads, batch size, OCR languages, pixel bound, device), then the operator's options, and the synchronous exar ones run `exar.ingest.Offloaded` in `pipelines.POOL`, an `exar.ingest.ProcessPool` that keeps their models warm in its workers and replaces a crashed, hung, or worn out worker, failing only its task (`compute.task_timeout_seconds` bounds a task, `compute.worker_max_tasks` the tasks of a worker).
A task that outlives `compute.task_timeout_seconds` has its worker killed and replaced, and a worker is replaced after `compute.worker_max_tasks` tasks against leaks.
Its workers start with `exar.ingest.limit_worker`, which splits the thread budget among them and applies the pixel bound to every image Pillow decodes, also those embedded in Office documents.
`pandoc` runs with `--sandbox`, so it reads no include or linked image a file names, and `pandoc` and `pdftotext` run with a timeout in a session of their own, so a killed worker leaves neither behind.
The cheap chunkers, `none`, `token`, `sentence`, `table`, `recursive`, and `markdown`, run in a thread of the server instead, since they load no model, so they never queue behind conversions or count toward `compute.worker_max_tasks`, whose replacement of a worker reloads the conversion models.
Only `semantic` keeps its embedding model warm in the workers.
Marker, MinerU, and the fast, code, neural, and late chunkers are gone with the move to exar.
Each registration carries its `label` and `description` as `Localized` text, so `GET /admin/pipelines/{stage}` lists the installed pipelines in the request's language and the frontend spells none of them.
The operator owns every pipeline: a document is converted and chunked with `auto`, or in a source's mirror with the source's own (`sources.catalog.conversion_of` and `chunking_of`), so nothing carries a pipeline choice.

## Workspace content

### The filesystem is the source of truth

The tree under `data/workspace/<store_key>/` is authoritative for document content, and the `documents` and `chunks` rows are a derived index.
`workspace.sync_entries_from_disk` is the one idempotent ingest path, which re-chunks only when the `content_digest` of the text or the `chunker` fingerprint of the options of its chunking pipeline (`pipelines.chunker_fingerprint`, after `exar.ingest.fingerprint`) changed, behind a `tools.entries.ContentStat` pre-filter that can cost a read but never misses a change.
The digest, fingerprint, and stat are cleared together by `upsert_document` and stamped together by `set_content_state` once the chunks are durable, so a null digest means "not indexed yet".
The fingerprints cover options, not code, so an update that chunks or embeds otherwise ships a revision clearing the stamps (see the migrations above), while `hivegent reindex [--workspace <key>]` clears them ad hoc, such as to recover one workspace.
A description with no prior row is stamped `origin = imported`.

`reconcile.reconcile_store` runs at boot over every entry on disk and every row, and drops rows whose description vanished.
Its walk prunes the build and vendor directories a listing hides by default (`exar.tools.common.DEFAULT_EXCLUDE_DIRS`, such as `.git` and `node_modules`), so it never indexes what the tools do not show.
It never deletes or renames workspace files, since a file without an entry is inert content rather than an orphan.
A store whose root is missing, or any directory of which cannot be listed, fails its reconciliation and keeps its rows, since an unmounted or stale share must not read as empty, while an existing empty root still drops them.
Likewise a description that cannot be stated for any reason but its absence keeps its row.

### What becomes a document

`tools.entries.is_description_file` is the content-versus-document policy: markdown files are descriptions, and every other file becomes a document only through an upload or a write, which convert it.
A file dropped into a workspace by hand without a description stays inert on disk, listed and deletable.
`formats.projection_for` is the one routing table for what projection a filename gets, read by uploads (`workspace.prepare.prepare_upload`) and rediscovered rows (`workspace.metadata._REDISCOVERED_KINDS`), so they cannot drift.

### Paths

A document path is prefixed by its workspace (`~` personal, `@<group>` group, `/srv` everyone's), and a relative path is shorthand for one under `~` (the `implied` flag of its `Mount`), while filter arguments without a prefix still span every workspace.
The global prefix is `store.GLOBAL_PREFIX` and nowhere else on the server, the prompts included.
A path leading with `/`, `~`, or `@` that names no known root is refused with the roots named (`tools.base.workspace_root_hint`), so an unknown group is never written under `~`.
The grammar is stated once per surface (`WORKSPACE_PATH_INSTRUCTIONS`, also the FastMCP `instructions`), and messages always render canonical paths (`workspace.paths._shown`, `store.scope.render`).

Access derives from the request's `User` alone (`User.scopes`, `User.workspaces`, `User.workspace`), and `UserDeps` holds that `User` rather than stores, so no route, run, or subagent can reach a workspace the token does not grant.
A scope is `GlobalScope`, `GroupScope`, or `UserScope` (`store.Scope`), each declaring its `kind`, and its key, the kind with its id if it has one (`global`, `group:<id>`, `user:<id>`), names the workspace directory and its SQL row, parsed back by `store.parse_scope`, and its prefix addresses its paths, split off by `store.split_scope_path`.
`User.scopes` is the one visibility rule (global, the groups, the user, in that order) that later scoped resources reuse.
HTTP and MCP build the `User` through the same `auth.user_from_claims`.
Writes span the personal workspace, the writable groups, and for an admin the global workspace, reads every visible one.
The global grant is the admin role, decided in `User` where every grant is, so no route or UI decides it.
Every workspace is a `workspaces` row keyed by its scope's key, the global one included, which names the user, group, or source it belongs to, if any, and goes with it, so a document names its workspace by `documents.workspace_key` alone, unique by its stem there, and deleting an owner takes its documents along.
A document write materialises that row and its parent first (`db.workspaces.ensure_workspace`), and the `workspaces.parent` check derives the key from the parent, so a row can neither claim another scope's key nor name two parents.
A `Workspace` carries a `store.Grant`, `READ`, always for a source's mirror, or `WRITE`, and the workspace lock (`workspace.locks.root_lock`) and every document row write (`db.documents._writable_filter`) refuse anything short of `WRITE` with `AccessDenied` (403), the global workspace's included, which only an admin's `User` grants.
A `Workspace` has no public constructor, and neither `dataclasses.replace` nor a copy changes its grant, so a grant comes only from a `User` or from a `store.HostGrant`, which only the CLI, the startup, a source's sync, and the admin reindex build and hand down (`reconcile.reconcile_all`, `sources.sync.sync_source`, `hivegent reset group`).
Owned rows are named through one predicate per table (`_workspace_filter`, `_owned`), and the conversation and memory repositories take a `User` or an `OwnedConversation` rather than an id string.

### Access is scoped by construction

Access is never a check a caller has to remember, it is a value the caller can only obtain through the check.
A `User` is the one principal, built only by `auth` (`auth.CurrentUser` in every route), and every grant derives from it.
Every function acting on a user's data takes their `User`, never an id string.
An `OwnedConversation` is the proof of a conversation, built only by the checks in `db.conversations`, and it names its owner by id without acting for them, so code that needs authority takes the `User` beside it, and `UserDeps` refuses a conversation that is not its user's.
A conversation is its owner and id together, which equality and hashing compare, so state kept per conversation, such as the running turns, is keyed by that value and is per owner without a check, while an id alone addresses nothing.
State kept per user in the process is partitioned by owner, as the job registry is, so a lookup by id cannot reach another owner's entry.
No route acts for another principal: wiping another user's or a group's data is `hivegent reset {user,group} <id>` on the host, which builds that user with no groups or roles, as no token is held for them.
A service principal (`User.kind == "service"`, the Teams bridge, built from a token of `auth.service_client_id`) has its capabilities by construction: `User.scopes` yields the global scope alone, it is never admin and writes nothing, `UserDeps` refuses it any mode but `read` and `build_run_prefix` forces that mode, `guards.OFFERED_TOOLS` withholds the toolsets built `people_only` (memory, conversations) on every agent, the memory and conversation repositories refuse it (`User.require_person`), the MCP endpoint refuses it, and every route but the chat route sits behind `auth.get_current_user`, which refuses it.
Its one identity holds every Teams user's conversations, which is why nothing that lists or reads them by id is reachable for it.
Deleting conversations stops their turns first (`conversation_files`), so no caller can forget it.
A `Workspace` carries its grant and a request derives it from its `User` alone, and an `OwnedConversation` has no public constructor either.
A skill or assistant library is changed under a `libraries.LibraryGrant`, the write grant of its scope's workspace, which wiping a scope's data takes too (`server.common.delete_scope_data`).
`tests/unit/test_access_scope.py` keeps the builders of a `User`, a `HostGrant`, and a `Reach` the only ones.
Disk reads resolve through `Workspace.locate`, which folds `..` and refuses any path through a symlink (`exar.memory.mounts.canonical`), so nothing escapes a workspace and no alias is followed, `query_table` runs on a DuckDB database without file access, and `jq` refuses module directives and runs without the server's environment.

A group is identified only by its OIDC groups-claim ID, in the `groups` row, the `group:<id>` workspace key and directory, and the `@<id>` prefix.
`auth.parse_group_claims` is the one place that knows how a provider spells the claim, and display names are client labels only.

Path strings are folded to NFC wherever they enter (`text.normalize_unicode`), since macOS hands out decomposed filenames, while file content is never normalized, and a file dropped into a workspace by hand keeps its spelling.

### Text, encodings, and what may be written

Every read of user bytes goes through `exar.text.decode` (BOM, strict UTF-8, CP1252 fallback, `None` for binary), so a hash taken by a read tool matches when an edit tool re-reads the file.
Writes are always UTF-8, and a transcode is reported rather than applied silently.

A document is writable exactly when it is readable as text, since both sides ask `formats.vision_media_type` first and the decoder second, and every refusal ends in `tools.base.sidecar_hint`, which names the `<stem>.md`.
A markdown description is written and re-indexed in place under the workspace lock.
Any other file is an original, so the gateway converts the new bytes as an upload would (`changeset._convert`) before taking the lock, checks the hash again under it, and leaves the previous entry intact on failure.

Creating a file decides by name, since it has no bytes yet: `formats.writes_as_text` refuses a suffix on `formats.BINARY_SUFFIXES` and admits every other, so `.csv`, `.html`, and `.svg` are created as text.
A new original must claim a free stem, and a binary is replaced by uploading.
The refusal (`formats.BINARY_WRITE_REASON`) comes from `tools.mutations._resolve_text_target` before the approval prompt, and from the sandbox mount when a program writes a new file.

Every served and shown media type comes from `formats.media_type`, derived from the path rather than stored.
It asks Python's built-in table with the declared image, video, and LLM tables and the formats documents embed laid over it, never the host registry, since that one differs between machines and mislabels source files.
The document routes serve a file as plain text when its type is unknown or textual and it decodes, and anything else under its own type as a `nosniff` attachment, so an SVG reaches an `<img>` as an image but never renders in a tab.
The client previews any original whose type is an image or a video, an SVG included although it is indexed as text.

A whole write to a delimited file is checked against its header width by `tools.mutations.check_delimited_rows`, with the separator from `formats.DELIMITERS`, the table `query_table` reads with too.
Edits are not checked, since they change a file they did not build.

### A conversation's `/tmp`

`/tmp` holds a conversation's working state that is never a document: intermediates, state for a later `run_python` call, scripts, and spilled tool results.
Each conversation owns `<data_dir>/conversations/<user_id>/<id>/` (`OwnedConversation.folder`), outside every workspace, holding its `tmp/` and the `changesets/` its programs staged.
Ownership comes from the `conversations` row, never the path, and an `OwnedConversation` is obtained only from the database or by `open_conversation` for an absent id (see "Access is scoped by construction"), with a `UUID` id whose canonical string names the folder, so no caller can name another user's folder.
The user level keeps what a run writes after its conversation was deleted with its user, so another user who claims the freed id never reaches it, and the sweep removes it once idle.
`tmp.tmp_dir` is the one place an id becomes a path, and every `{conversation_id}` route resolves through one dependency.

`/tmp` is a root beside the workspace roots: `UserDeps.search_paths` appends the search path of `tmp.tmp_root`, so the read tools reach it with no code of their own, and it is never reconciled, indexed, searched, filtered, or announced.
A subagent shares its parent's `/tmp`, while a caller without a conversation (MCP, the admin tool console) has no `/tmp` root.

Every `SearchPath` carries its `mount`, the one path abstraction, `exar.memory.mounts.Mount`, for the read tools and the routing of a canonical path (`exar.memory.mounts.claim`), with its filter and its `CommitPolicy`, and a root of the gateway is a `SearchPath` with what only a commit needs, and `CommitPolicy.reserves` names what no ordinary change may touch, the `.assets` payloads and `/tmp`'s reserved `.tool-results/`.
`Direct` (`/tmp`) is written straight, `ModeGated` asks where the mode does, and `Reviewed` asks in every mode.
`Workspace.policy` derives it from the scope alone, so no call can choose another: every workspace a user may write, their own, a group's, or the global one, is `ModeGated`, since choosing `write` mode is the user's consent to change what they may write, while a source's mirror is `Reviewed`.

### Effects and approval derive from declarations

Every tool declares its effect with `exar.tools.effect`, which `exar.pydantic_ai.toolset` puts on its definition (`"effect"` metadata), and a plugin tool without one is refused: `read` changes nothing beyond `/tmp`, `write` persists inside Hivegent, `execute` performs an action outside it.
Running code is no `execute` of itself, so `run_python` is a `read`, as its effects stay in `/tmp`.
`agents/policy.py` derives everything else in one place, the host's `exar` policy (`host_policy`) among it.
Read mode offers only reads and the tools of a toolset built with `gateway=True` (`write`, `python`), whose writes land through the changeset gateway and are narrowed to `/tmp` by `writable`, and a feature is offered only in the modes that offer all its tools, so no instruction names a withheld tool.
`host_policy` denies what a mode does not offer and asks before any write that is not a gateway write, which is `Reviewed` and how every memory write asks, and before every `execute` tool.
It is the policy of every exar run over a run's deps (`UserDeps.run_config`), which allows the agent invocations the host makes itself, and one `exar.pydantic_ai.EffectGate`, `guards.HOST_GATE`, applies the policy of the exar run it runs in on every agent and on `invoke_agent_tool`, which opens an exar run of its own, to the built-in, plugin, and external MCP tools alike: it hides what the policy denies and raises `ApprovalRequired` for what it asks, deferred by pydantic-ai once the arguments validated.
A tool marks a gateway toolset by its `gateway` metadata, which the policy reads off the `Action` the gate builds.
A gateway write is allowed by the policy and asks per item by its root's policy in its argument validator (`agents/tools/write.py`), which plans the changeset first and shows its summary.
A program is handed only reads, and the MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) follow the effect.
`/tmp` commits through the same changeset gateway as a `workspace.operations.Root` without store (`tmp.tmp_root`), so occupancy, chains, basis checks, and rollback exist once.
`/tmp` is writable in every mode, so the `write` feature is offered in every mode, while `WRITE_INSTRUCTIONS` and `apply_changes` are limited to modes that may write the workspaces and to runs with a conversation.
A move between `/tmp` and a workspace is refused by the gateway, since no one commit spans both.

`settings.tmp.max_bytes` caps a folder through its `Quota`, charged with what a changeset adds, so a refused change writes nothing and an over-cap folder may still shrink.
`GET /api/v1/documents/{filepath}` reads a `/tmp` path when given a `conversation_id` the user owns.

`conversation_files` ends a folder's life, since no row cascade reaches it: deleting conversations removes folders and then rows, a reset removes every folder, and `DELETE /api/v1/tmp` empties only each `tmp/`, keeping pending approvals.
`sweep_conversations` judges a folder idle once nothing below it changed for `settings.conversations.ttl_hours`, runs in a background task after the migrations and every `sweep_interval_hours`, and checks each folder again under its `/tmp` lock.
An idle folder whose conversation exists loses only its `tmp/`, since a staged changeset is the user's pending approval, which goes only when answered or with its conversation, while the folder of a conversation that no longer exists goes whole.

`TMP_INSTRUCTIONS` is shared by the `python` and `write` features, and `PYTHON_INSTRUCTIONS` names `/tmp` as the home of a rerunnable `.py`, since a `.py` in a workspace is an original that gets chunked.
The write receipt for a `/tmp` `.py` points at `run_python`'s `script_path` through a `MutationHint`.

- TODO(binary): accept binary files of any suffix in `/tmp`, with `run_python` reading and writing bytes under a per-file cap.
- TODO(binary): let `run_python` write or move a binary into a workspace as a staged item that runs the upload conversion at apply time and shows as an upload in the approval.

### Notifications

Every workspace change reaches clients as one `tools.changes.WorkspaceChanged` on the SSE feed, carrying the changed scopes plus moves and deletes, so a client can follow the paths it keeps.
`apply_changeset` announces one per commit, whoever commits (a route, a job, the agent, MCP), and every other mutation (rechunk, wipes, reconciliation) announces through `workspace_events`, or clients stay stale.
A feed follows the `store_key` of every workspace its user may read, so a group change reaches every connected member and the acting tab alike, with no client excluded.
Each follower sees only the scopes and paths it can read, a move out of its sight arriving as a delete.
Jobs stay per owner, so only the submitter sees an upload's progress, while everyone sees its entries land.
Every committed change to a user's conversations (a turn, a title, a delete, an import) publishes one `jobs.ConversationsChanged` to that user's feeds from `db.conversations`, so every tab and device lists them again.
Events are transient, so the client re-reads every scope it holds and its conversation list on each handshake.

### Preparing for a read-write shell tool

A future shell tool will run native commands in an isolated session, and an explicit checkpoint folds an approved session diff back into the filesystem and index.

- TODO(shell): run commands in a dedicated sandbox runner with a narrow session API, never through a shell or container socket in the backend.
- TODO(shell): pick the runner from the workspaces of pydantic-ai-harness, which the `Shell` capability uses as they are.
  `SSHWorkspace` wrapped in `BubblewrapSandbox(network=False)` runs on a dedicated Linux host: only the working directory is writable, `/tmp` is private, `/run` is empty, and seccomp blocks every socket, but the runner account must own nothing else, since `bwrap` does not hide what it can read.
  A self-hosted `OpenSandbox` server, with Docker containers on one machine or pods on Kubernetes behind its own API, comes closest to a narrow session API, with `allow_internet_access=False` and resource limits.
  A microVM of `SmolSandbox` or `BoxLiteSandbox` isolates more strongly on the backend host itself, if the host supports it.
  `DockerSandbox` in the backend needs the container socket and is ruled out, and so are cloud sandboxes, which take the data off premises.
  On macOS, which has no `bwrap`, development uses OpenSandbox or a microVM.
- TODO(shell): run each session as an unprivileged user seeing only the store's working copy at `/workspace` and the conversation's `tmp.tmp_dir` at `/tmp`, with no secrets, sockets, capabilities, or devices, a read-only root, and explicit resource limits (`tmp.max_bytes` for `/tmp`). `exar.subprocess.run` is unsandboxed and only safe for fixed-argument tools.
- TODO(shell): run against an isolated copy or overlay of `/workspace`, so the workspace lock is never held while commands run, with a copy-based fallback for macOS. `/tmp` is written directly, as the Python sandbox does.
- TODO(shell): compute the session diff in trusted runner code, surface it for approval, and make checkpoints explicit.
  `exar.pydantic_ai.snapshot` and `changes` compare the files of any pydantic-ai workspace before and after a session by their digests, and the changes map onto the operations of `tools.changes`.
- TODO(shell): fold the diff back through `workspace.apply_changeset`, reusing the entry rules of `tools.changeset.stage_changes`.
- TODO(shell): keep an entry whose sync failed out of retrieval, retain its dirty state for retry, and report the checkpoint unsettled until every entry succeeds.
- TODO(shell): decide which shell-created binaries and converter-backed formats become entries at fold-back.
- TODO(shell): keep all workspace access behind `Workspace.workspace_dir` and all `/tmp` access behind `tmp.tmp_dir`, so each mount root is injected in one place.

## Conversations

### Conversations live in a pydantic-ai ConversationStore

`db.conversations.PostgresConversationStore` implements pydantic-ai's `ConversationStore` on two tables: a `conversations` row is a record, a `messages` row an entry.
The record names its active history by `head_id`, keeps the tips of the histories it left in `branches`, the calls a paused run awaits in `deferred_tool_requests`, the conversation's `usage`, and a `revision` every save checks, so pydantic-ai decides what a save appends and where a rewritten history branches.
The owner is the store's `scope`, so a conversation saved for another user loads as absent.
The title is the record's `title`, which a save leaves alone and only `ConversationStore.update` changes, through `write_details`, one `UPDATE` that also checks the revision and the title it expects, so a generated title never replaces one the user set while it was generated.
A conversation keeps no other `metadata`.
pydantic-ai reads a missing `scope` as every owner's, so the store refuses one for every read, list, and save, and only `owner_of`, the ownership check's read, names none.
Messages are never changed or moved once stored and their ids are minted by the store, while the id the client knows a message by rides in its metadata where pydantic-ai's UI adapters keep it (`turns.stamp`), so `dump_messages` gives it back and the adapter's server-held history matches a resubmitted message by it.
A message names the conversation that stored it, so deleting a conversation deletes its messages, since nothing forks one.
`read_history` walks a history up from its head in one recursive query.
pydantic-ai stamps the whole system prompt on every request, about 21k characters, so a conversation keeps the prompt of its first request once, in `Conversation.instructions`, and a request sent under it is stored without its `instructions` key and read back with the prompt, while one sent under another prompt keeps its own (migration `65f6705faca2` converted the stored ones).
Stored messages never change and a conversation's prompt is set once, so the store keeps the messages it reads or writes in a `cachetools.LRUCache` bounded to 64 MiB of stored JSON, and their prompts in one of 8 MiB, and a history it holds whole needs no query.
The ownership check reads `user_id` alone, and the prompt is a deferred column, loaded only with a history.

A turn loads its conversation, runs on its history, and saves it with the turn's messages appended (`routes.conversations.compaction._save`).
The client sends only its new message plus `trigger` and `messageId`, and the history always comes from the store.
An edit or a regenerate runs on the history that shows before the message it replaces (`turns.kept_before`), so the save finds the history rewritten and keeps the old turn as a branch.
A compaction made for a prompt summarizes only the history before it, so an edit or a regenerate of that turn keeps it, on a copy in the new reply.
`ChatAdapter` keeps only the client's user messages, since appending the approval resend would duplicate a `tool_call_id` and make the model reissue the call.
The route runs the agent with that prompt as its user prompt (`ChatAdapter.prompt`, `agent_events`) rather than through `adapter.run_stream`, which appends it to the history.
A run merges consecutive requests, which would merge it into a stored request it follows: the returns closing an abandoned or errored turn, or a prompt whose run failed.
`Replay` measures the replayed history as the run normalizes it (`repair_messages`), so the turn's messages start where it ends.

The adapter's `conversation_store=` option (pydantic-ai 2.68) loads, runs, and saves a conversation the same way, and this store plugs into it, but the route does not use it.
It stores no failed or stopped run, while a reload here shows what such a turn streamed, and it denies a call with the user's note as the whole refusal, without `denial_message`, the approval notes, or the closing of an abandoned call.
The route follows its rules for answers: a decision must answer a call the record awaits an approval for, or the request is refused with `409`, and an answer the client re-sends for a call already answered is dropped (`ChatAdapter.resume`).
A request without a prompt only continues a run by answering it, so one that answers nothing the record awaits is refused too, rather than closing the calls that do await an answer and running the model again.
Approvals and answers end the run and resume it in a new one rather than waiting inside it with `RequestInput`, since a waiting run would hold the conversation's turn slot, lose its question on a restart, and stream nothing the AI SDK's approval flow reads, which only sends a decision once the stream that asked has ended.
The UI ids it matches an edit or a regenerate by are the ones stamped here, and an edit may reuse the edited message's id, as the AI SDK does, or send it under a fresh one with `messageId` naming it.

### The transcript is one projection of the store

The store holds pydantic-ai messages only, and `vercel.dump_transcript` is the one transcript of them: a reload reads it, and every turn ends on it.
It projects the active history as the client streamed it: the messages sharing a UI id are one `UIMessage`, each model response opens a step, a compaction made for a prompt shows in the reply to it, where it streamed (`turns.display_order`), and an unanswered call shows as it streamed, an approval request only for a call the record says awaits an approval (`_reopen_calls`).
The memory read a window opens with carries the server-only `hidden` metadata key and is left out, since the user never asked for it.
A turn mints the id of its reply before it runs and announces it as the stream's message id, so the client streams the reply under the id a reload shows.

A turn ends on a transient `data-turn` part carrying the transcript from the first message the turn stored on (`turn_snapshot`), which the client swaps in for what it streamed.
It starts at the reply before the prompt when the turn closed the calls that reply awaited, so the overtaken request updates too.
The run registry places it after everything the turn streamed, a stop included, but ahead of its errors, since the AI SDK stops reading at an error chunk.
It is projected from what the turn saved, from its first UI message on alone, only once the save succeeded, so a turn that could not be stored stays in the client as it streamed, to be retried, rather than being replaced by the history before it.
So the transcript a client keeps after a turn is the stored one, and any difference between the stream and the store would show as a visible swap.
`tests/unit/test_turn_parity.py` pins that there is none: it drives turns through the chat route over pydantic-ai's `InMemoryConversationStore` and records each client's messages and chunks as fixtures under `frontend/src/test/fixtures/turns/`, which `turn-parity.test.ts` replays through the AI SDK and compares with the swapped-in turn.
`UPDATE_FIXTURES=1 uv run pytest tests/unit/test_turn_parity.py` rewrites them.
An attached image keeps its filename, which pydantic-ai's adapter drops, as the identifier of its `BinaryContent` (`ChatAdapter` in `server/vercel/adapter.py`), which no model reads in a user prompt.
`tests/unit/test_contract.py` records the frontend's contract the same way, the JSON Schemas of the API and of what reaches the client outside it, plus every tool name, as `frontend/src/test/fixtures/contract.json`, which `contract.test.ts` checks the zod schemas, tool icons, and labels against.

Migration `65f6705faca2` moved the message tree into the store: the newest leaf became the head and the other leaves the branches, every message got a fresh id and the UI id the client knew it by, and the paused requests a response carried moved to the record.
A compaction stored ahead of a prompt showed at the end of the reply before it, so it keeps that reply's UI id and keeps showing there, since a compaction moves past the prompt after it only when it belongs to that prompt's reply.

### Clients choose conversation and message ids

The client mints a conversation's id and the id of every new user message as random UUIDs, so it never waits for the server to name what it addresses next, and no response header carries an id.
Every id the app or a client mints is a native `uuid` column read as a `UUID`, parsed at the boundary (`config.parse_uuid`), so it has one spelling in the database, on disk, and on the wire.
`POST /api/v1/conversations/{id}/chat` is the only chat route, and `open_conversation` resolves its id: an owned conversation is used, an absent id opens a new one, and an id that is no UUID or another user's answers 404.
Turn preparation loads the conversation afresh and claims an absent record before deriving its replay state, so an earlier existence check cannot hide history written by a racing request.
The store's insert decides between racing claims, and the second replays the returned conversation if the caller owns it, otherwise it answers 404.
A new prompt's id and attachments are checked before claiming, and a compaction of an absent conversation is refused, so a refused request leaves no empty record.
`GET /api/v1/conversations/{id}/messages` answers `[]` for an absent id, so a client opens a new chat at its own id, and a deleted conversation's id is absent again.

The new user message keeps the client's `UIMessage.id`, refused with 422 unless it is a UUID, and with 409 when the history it runs on holds it, so a message is never stored twice in one history.
A regenerate, a compaction, and a post-approval continuation carry no new user message.

### A turn runs apart from its request

A turn runs as a task of `runs.runs`, at most one per conversation, from its preparation to its last chunk, and keeps every chunk it streamed until it ends.
The registry keys a turn by its `OwnedConversation`, the owner and the id together, so another user addressing the same id finds nothing to follow or stop, even before the turn claimed the conversation's row.
The request that started it follows it, and a client that leaves only stops following, so a closed tab or a sleeping phone loses no answer.
`GET /conversations/{id}/stream` follows the running turn again from its first chunk, the AI SDK's resumable stream, and answers 204 while none runs, so a client reloading after the turn ended reads it from `.../messages`.
While a turn runs, `.../messages` shows the history the turn runs on with the client message the turn answers or continues, its new user message or the assistant message carrying a continuation's decisions, since the turn is stored only once it ends.
A reload so shows the prompt the followed answer belongs to, and the client replays a continuation into the message it continues (`resumeStreamIsReplay: false`) rather than into a second one.
A client that finds no turn to follow reads the history again, since the turn may have ended after the history was read.
A new conversation's record is claimed before its first turn runs and titled by its prompt, and the list includes the conversations with a running turn (`runs.running`), so a tab closed mid-turn finds the conversation in its history and follows the turn again from there.
A request takes the conversation's slot before it prepares or writes anything and gives it up when it is refused, so a retry of the running turn's request, the same user message id, follows that turn, and any other request is refused with 409 untouched.
`DELETE /conversations/{id}/stream` is the only way to stop a turn, preparing or running, and returns once the turn stored what it got to and left its slot, as the shutdown does for every running turn before the database closes.
The followed stream ends by itself once the turn is stored, so a steering message the client queued is sent to a free slot, and the client stops reading on its own only when the stop failed.
Every write of a turn runs to completion through `exar.helpers.shield_to_completion`, so a cancel during it ends the turn only once it is stored.
A changeset is the longest of them, since `apply_changeset` shields the file swap, the commit, and the embedding of every written document as one unit, so that a cancel never settles between them and leaves new markdown wearing its predecessor's rows.
A stop or a shutdown during it therefore waits for the embeddings, bounded by the embedding model's time over the written documents, and the shutdown has no other timeout.
Splitting the index off as a follow-up that boot reconciles would shorten the wait, but it would let the shutdown abandon half-indexed documents, so the latency is kept for the guarantee.
A turn that fails in its stream or in settling ends on an error chunk and is logged, so followers and the JSON answer report it instead of an unexplained end.
Deleting a conversation stops its turn first, and replacing it with an archive is refused with 409 while a turn runs, since the turn still writes to the history it would replace.
`Accept: application/json` waits for the turn and answers the active path, its last message being the answer, or a 502 problem when the turn failed, so a client without a stream parser drives the same loop.
The registry lives in the process like the maintenance flag, so it holds for the single-process deployment, and a turn ends with the process.

### A paused run stores what it awaits

A run that ends on `DeferredToolRequests` saves them on the conversation's record, as `Conversation.deferred_tool_requests` carries them, since the messages show which calls are unanswered but not whether each awaits an approval or an answer.
What each was deferred with, the changeset summary of an approval, and its validated arguments are left out (`RunEnd.deferred`), since the response that asked stores them once for the client and a resume validates the call again.
The next request resumes against them: `ChatAdapter.resume` keeps only the decisions and answers for calls still pending, as the client re-sends every settled card, and builds the results with `DeferredToolRequests.build_results`, which refuses a call answered with the other kind of result with a 422.
`ChatAdapter.deferred_tool_results` is therefore always `None`, so the base adapter never resumes from the client's claims alone.
The client receives only what each `ApprovalRequired` carried and the arguments each call runs with (`DeferredToolRequests.validated_args`, repaired and validated), stored on the response as `approvalMetadata` and `approvalArgs`, so the user approves what runs rather than what the model sent.

An approval or question the user never answers is closed by the next turn (`abandon_pending_calls`), stored before its prompt as part of the reply that asked, since pydantic-ai would otherwise repair the dangling call as "interrupted" and the model would reissue it on every turn.
The approval is denied and recorded as the user's denial without a note, and the question dismissed.

Persistence is hard-fail: a failed save surfaces as a trailing error chunk, and is only logged on a cancel.
`run_and_persist` hands over exactly what `capture_run_messages()` holds.
It closes the turn's chunk stream in its own task however the turn ends, closed early included, so the generators of the run, exar's `traced_events` holding its session and context variables across their yields, never finalize from the garbage collector in another context.
A turn that never finished first closes its open calls as the stream closed them (`close_orphan_tool_calls`): a call a run error cut off fails with the stream's own words, one a stop cut off is `interrupted`, which the client shows as stopped.
It also records the error for the reloaded banner (`record_turn_error`), while a clean finish is stored untouched, so a pending approval reloads as one.
The usage of a run that finished is added to the record's, and a run that failed or stopped adds none.

### Client-posted messages are validated strictly

The Vercel AI request models forbid extra fields, and so does `ChatRequest` around them, so a part the SDK sends that pydantic-ai has not transcribed, or a setting the server does not know, fails the chat and import routes with a 422.
Such drift is fixed upstream, never by relaxing `extra='forbid'`, which is what stops a malformed `approval` from matching the unanswered variant and releasing a gated call.
`tests/unit/test_client_messages.py` pins the shape a real client posts.

### The client receives only what it declares

The Vercel adapter streams and dumps run state, such as model setting deltas and enqueued messages, as data parts for a client that sends the history back, while this client sends its new prompt only.
`CLIENT_DATA_TYPES` in `server/vercel/transcript.py` mirrors the data parts the frontend declares, and every other one stays server side, live and on reload alike, so a part a pydantic-ai update adds never reaches the client unannounced.
The transcript also decides for every kind of request part whether it shows, ending in `assert_never`, so such an update fails type checking until the new kind is decided.
The chat debugger keeps the client-held history the adapter is built for, and is left unfiltered.

### Tool approvals round-trip symmetrically

A decision's `reason` is the user's optional note, and the model-facing text is worded in the backend, in English.
A denial becomes `ToolDenied(denial_message(note))`, and an approval's note reaches the call as `RunContext.tool_call_metadata`, which `agents.approval.ApprovalNotes` appends as one more item of the return's content list, after pydantic-ai-harness's `ToolOutputLimits` bounded it (`wraps=[ToolOutputLimits]`).
It is not `ToolReturn.content`, which would be stored as a user turn nobody typed.
`turns.record_approvals` stamps each decision the user made on the request carrying the returns, and `dump_transcript` restores it, so `approval.reason` always means the user's note.
That metadata is UI-owned, never sent to the provider, and rides on the request node, since `ToolReturnPart.metadata` is the tool-output chunk channel.
`ChatEventStream.handle_run_result` stores what each `ApprovalRequired` carried under `approvalMetadata`, so the prompt shows the same server-resolved paths live and on reload.
An approval is bound to that changeset: `ChatAdapter.resume` hands the call the `changes_digest` of the summary the response that asked stored under `APPROVED_CHANGES`, as MCP does with the one it confirmed, and the write gate plans the call again and refuses it once the changes differ, so the model asks about the current ones.
An approval overtaken by a new message is a denial, stored by `abandon_pending_calls`, which the frontend shows as denied once a later message overtook it, live and on reload alike.

### Questions are deferred calls answered by the client

`ask_user` raises `CallDeferred`, so the run ends with the questions pending and the client sends the answers as the call's output, validated against `AskAnswer` by `ChatAdapter.question_answers`.
A dismissal or an abandoned question is a failed return worded by the server (`DISMISSED_QUESTIONS`), and `dump_transcript` shows a pending question as `input-available`, as it streamed.

### Compaction is a turn of the conversation

A compaction is a message of the conversation it compacts, a `ModelResponse` holding one provider-less `CompactionPart` whose `content` is the summary.
The store keeps every message, so branches, exports, and the transcript stay whole, while pydantic-ai treats the part as the boundary of the instruction window and of the tools it derives from history, and the Vercel adapter round-trips it as a `data-compaction` part.
It is stored as a response rather than the request pydantic-ai gives a summary the application wrote, since a run merges consecutive requests, which would merge the compaction with the prompt after it and the returns before it.
An import loads a summary into the request after it, so `load_archived` splits it off as a response again.

The mechanism is `exar.pydantic_ai.compaction`, and Hivegent supplies its summarizer, `PrefixSummary` (`agents/compaction.py`), which appends the localized `COMPACT_PROMPT` to the conversation's own message list under the same `RunPrefix` as its chat turns, so the provider's KV prefix cache covers everything but the prompt.
That is why a compaction runs inside the chat route, `build_run_prefix` composes the prefix for both, and the history is loaded from the database rather than the browser.

Room is made by dropping messages from the tail, which keeps a shorter prefix of the cached block.
Sizes come from `ModelResponse.usage`, so no tokenizer or context window setting is needed, and only prefixes where every tool call is answered and that do not end on a fresh user prompt are offered.
The first attempt sends the longest prefix the endpoint has served, and only a refusal sheds the summary's completion cap plus a prompt reserve, for at most three attempts.
A subagent's history is compacted the same way, under the subagent's own prefix.
`SUMMARY_MAX_TOKENS` is clamped to the request's own `max_tokens`, the summary runs with reasoning off, and `COMPACT_PROMPT` bounds the summary by structure, since a length cap makes the model drop sections.
The tools stay declared so the prefix stays cached, and a summary that calls one anyway is asked once more with the tools withheld, at the cost of that request's cache, rather than failing the compaction.

A compaction fires only after the provider refused a request (`compaction.is_overflow`, exar's `is_context_overflow` or `is_server_overflow` for llama.cpp and SGLang), never on a forecast, since the window, reserve, and threshold are not reliably known.
`compact_on_overflow` then compacts the run's history at `compaction_cut` and retries, and exar's `Compacted` event shows it live.
The cut goes after the refused request where that closes the history, so the summary covers everything the client has seen and the retry continues from it with an empty request.
A refused prompt is never summarized, so the cut goes ahead of it and the retry sends it again.
A regenerated prompt is stored already, so its compaction is stored ahead of a copy of it, on a new branch, since stored messages never move.
Wherever a compaction made for a prompt is stored, the transcript shows it in the reply to that prompt, where it streamed.
A cut is one of those same prefixes, so the summary and the window agree on where history can be cut.
A window that overflows on its own is not compacted again, and a cut past the latest prompt waits for a model to answer it, so a prompt is never summarized with only the memory read reported beside it.
A response cut short mid tool call has already streamed, so it fails the turn instead.
A chat request flagged `compact` carries no message and compacts the history at the same cut, which is the header's button, and streams it as the `Compacted` event a run's own compaction streams as.
A history ending on a failed prompt is compacted ahead of it, on a new branch, and one ending on an open approval or question is not compacted, so the request leaves it pending.

exar's `CompactionWindow`, carried by every agent over the user's deps, sends every request the window from the latest compaction onward, with the compaction as a request, which pydantic-ai renders for every model as user text in `<conversation_summary>` tags behind a provenance line (#10573).
It renders from the stored message alone, so every later turn sends the window byte for byte and the provider caches it like any other prefix.
The window stays, since only an adapter that honors compaction boundaries drops the history before one, which `OpenAIChatModel` does not (#7289).

### Run state appends its changes

The instructions and tool schemas precede the whole history, so anything rendered into them that changes during a conversation has the provider reread all of it.
The date, the mode, and the document scope (`agents/context.py`) are therefore `on_change='append'` instructions, and nothing else of the instructions changes with the day or the request beyond the language: the first value stays in the prefix, and pydantic-ai appends each later change to the history as an `InstructionDeltaPart`, persisted with the turn, so a change only extends the cached prefix.
The UI adapter leaves these changes out of the messages the frontend receives.
Instructions carry operator authority, so only state the application controls goes there, a fixed mode note and the paths the user selected.
The modes that may write compose the same instructions and tools and differ only in their mode note, while read mode drops the write features and starts a new prefix.
The chat templates of the self-hosted runtimes reject a `system` message after the first user turn, so the self-hosted profile turns `supports_inline_system_prompts` off and a change goes out as `<system>`-tagged user text.
Both change only between turns, so that text joins the user's prompt: a user message in the middle of a turn is what Qwen's template takes for a new query, rendering the steps before it without their reasoning, which voids the cache.
pydantic-ai explains that tag in the system prompt, and every model is built with `framing_announcement='always'` (`llm.create_openai_chat_model`), since its default adds the explanation only from the first request that needs it, which rewrites the cached prefix mid-conversation.
`tests/unit/test_prompt_cache.py` drives the production vLLM model through stored turns and asserts each request extends the previous one byte for byte and adds no user message mid-turn.

### Memory is a tool result

The memory is model-written, possibly after reading a web page or a document, so it never becomes an instruction.
`MEMORY_NOTE` (`agents/tools/memory.py`), an `exar.pydantic_ai.ToolNote`, reports it as a synthetic `read_memory` call and result, enqueued into the first request of a conversation, and of every window a compaction opens, and persisted with it, hidden from the transcript, and the model can call `read_memory` again after a change.
`write_memory` appends to the memory or replaces one exact passage, so a save never has to repeat what it keeps.
The page is an `exar.cbrkit.StorageMemory` (`db.memory.user_memory`) over a cbrkit `SQLAlchemy` storage of the `memory` table in plain-text mode, keyed by user id, its `content` the page, and scoped to the person's row, so a read sees only that row and a write outside it is refused.
It materializes the person's `users` row before a write, refuses a service principal, and searches the page by the words it shares with a query (`WordCoverage`).
The storage replaces a row by deleting and inserting it, so `created_at` and `updated_at` both read the time of the last write, which nothing reads.
The tool body runs only once `guards.HOST_GATE` let the call through, which for a write outside the changeset gateway means once the user approved it, so it writes the page directly.
A write names the page it edited as expected and is checked under a row lock, so a conversation saving in between makes the edit retry rather than be overwritten.
Every write asks the user in every mode, `write` included, since an instruction planted in a page or document and saved there would reach every later conversation, and the edit is tried on the current memory first, so the user only approves a change that lands.
`limits.max_memory_chars` bounds the memory, which keeps the note at the start of every conversation small, and an edit that shrinks an oversized memory is still accepted.

### Subagents are delegated to with pydantic-ai-harness

A chat turn is one native pydantic-ai run of the chat agent, streamed by pydantic-ai's Vercel adapter, with no runtime around it.
While `subagents.enabled`, its prefix carries the `SubAgents` capability of pydantic-ai-harness with its `TaskControl` (`agents.subagents.delegation`), over the subagents of `SUBAGENTS`: `explore` over the documents, `write` that edits them in a turn of the `write` mode, `conversations` over past conversations, and `web` while web tools are configured.
The prefix carries it, so the compaction's summary declares its tools too and keeps the cached prefix.
The main agent delegates a task with `delegate_task`, in the foreground of the call by default or in the background, and checks on, steers, cancels, or resumes it with `check_task`, `message_task`, `cancel_task`, and `delegate_task(resume=...)` within the turn.
These tools change nothing themselves, so `exar.pydantic_ai.DeclaredEffects` declares them `read` ahead of `HOST_GATE`, read mode offers them too, and each subagent stays held to the host policy of the turn's mode and to the tools the policy of the turn's deps allows, so a withheld tool is withheld from every subagent too (see Registered or excluded).
A background task's report reaches the main agent as a prompt framed by the harness, which `db.conversations.is_prompt` tells from the user's own, so it is hidden from the transcript and never counts as a user turn.
The output spill never sees that prompt, so `BoundedReport` cuts what every subagent reports to `llm.tool_output_max_chars` where it writes it, saving the whole under `/tmp/.tool-results/` (`guards.spill_text`).
The turn waits for the running tasks before it ends, and a turn that pauses for an approval cancels them, as the harness does.
A subagent is an agent of its own per turn, on the turn's model and deps, so it runs in the turn's mode and language, and answers the main agent in English as its instructions say.
It never asks the user: the harness's `DenyUnanswered`, one of its `shared_capabilities`, denies every call that would wait for an approval with the instruction to report it, so the main agent makes that change itself and asks for it.
An overflow is compacted, a crash reaches the main agent as a retry (`contain_errors`), a model error as a result it decides on (`on_model_error="return"`), and a timeout of `subagents.timeout_seconds` as a note in place of the result, followed by the subagent's last text (`keep_partial_output`).
At 80% of that timeout `ReportDeadline` refuses every further call of the subagent with the instruction to report what it found, as the call's result, so it ends with its findings rather than the harness's note, which stays the hard backstop.
Its tools and instructions stay as they were, so every request of the subagent keeps the cached prefix.
`subagents.max_depth` bounds the nesting and `max_running` the tasks running at once.
Subagents live within the turn: no later turn resumes them, and nothing of them but their steps is stored beside the conversation.

Every step of a subagent renders under the call of the main agent that delegated to it, live and on reload alike.
`TaskControl(forward_events=True)` forwards the events of a task's run as `TaskProgressEvent`s, and the assignment of a task names its call (`TaskAssignedEvent.tool_call_id`), so `ChatEventStream.handle_capability_event` streams every step, a reasoning, a message, or a tool call, as a transient `data-subagent` snapshot addressed to that call (`agents.transcripts.SubagentSteps`).
The turn's `ChatAdapter` owns that one `SubagentSteps`, so a task that timed out or was cancelled keeps the steps it got to, and a reload shows exactly what streamed.
At the end of the turn `SubagentSteps.record` stores them on the return of that call as its `data-tool-output` transcript, which the snapshot the turn ends on and every reload render as any tool's data, and the frontend renders a `delegate_task` call as it rendered `explore`.
Only the steps are stored, never the subagent's history, whose tool results and images would bloat every return past `max_data_bytes` and be loaded on every reload, so the chat debugger lists the steps after the call as an `agent` step, and the call names the task.
The debugger therefore shows a subagent's steps only, never its tool results.

### Budgets of a turn

`turn_usage_limits` bounds a turn's requests, tool calls, and, with `subagents.max_cost`, the cost of its priced models (see "Cost counts priced models only"), for the main agent and all its subagents together, since they count into the turn's usage, which the conversation's usage adds up.
Not pydantic-ai-harness's `WrapUpAtLimits`, whose prompt on the last request a self-hosted model reads as a `<system>`-tagged user message mid-turn, voiding Qwen's cache (see "Run state appends its changes").
`agents.guards.limit_notes` (`exar.pydantic_ai.LimitNotes` with localized notes) reads the same limits and each tool's retries, and names the limit that will stop the run once three quarters of either is spent.
The note arrives mid-turn, so it rides what the model reads next rather than a turn of its own: a failing call's retry prompt, or the first result after the request budget runs low, once per run.
Not the request-scoped system prompt of pydantic-ai-harness's `WarnNearLimits`, which the self-hosted profile sends as a `<system>`-tagged user message mid-turn, voiding Qwen's cache (see "Run state appends its changes").
It also bounds a retry prompt to `llm.tool_output_max_chars`, which `ToolOutputLimits` leaves alone since it bounds only returns.
It rides on every agent, so a subagent is warned too.
Every agent toolset is built by `AgentTools.build`, whose `timeout` defaults to `llm.tool_timeout_seconds`, since pydantic-ai applies an agent's `tool_timeout` to its own tools alone, and a tool's own timeout overrides it, which is how `run_python` declares `SELF_BOUNDED` to bound its host calls.
`invoke_agent_tool` runs a tool through its feature's toolset, so the MCP endpoint and the debug route are bounded the same way.
Each purpose runs its own named agent (`chat`, every subagent by its name, `summarize`, `title`, `complete`, `debug`, `report`), since the name is what Logfire attributes spend to.
Every one retries a transient model error, a rate limit, an overloaded or failing endpoint, or a dropped connection, with `RetryOnModelErrors`, which waits as long as the endpoint asks and replaces the OpenAI SDK's own retries.
Every agent over the user's deps also tells its model when earlier turns came from another one (`LabelCrossModelHistory`), since a turn may pick another model of the catalog than the turns before it.
Every one also tells its model to change its approach when it repeats itself, across calls or within one streamed response, its thinking included (`DetectLoops`), refuses a web search or fetch it already made in the run, and caps the tools `tools.budgets` names (`ToolBudgets`), each refusal a tool result the model reads rather than the end of the run.

### Runs are exar runs

Every pydantic-ai agent is instrumented into exar's tracer provider at startup (`observability`, `exar.pydantic_ai.instrumentation`), so the trace of an exar run holds its model and tool spans whether or not Logfire exports them, and Logfire, configured first, only adds exporters to the provider exar records from.
The instrumentation stays on without an exporter, since exar's experience reads the trajectories of runs from these spans, while exar keeps spans in memory only for a trace a caller asked for.
The spans carry no message content unless `logfire.include_content` opts in, and never binary content, so an exporter receives metadata by default.
A chat turn is the root invocation `chat` of an exar run under the config of its deps, `UserDeps.run_config` (`exar.pydantic_ai.traced_events` around `run_stream_events` in `server.routes.conversations.turn`), which opens before the first event so the agent run nests below it, ends on the run's output, and fails with the run's error, which passes on to the UI adapter that streams it in-band, before the turn is persisted.
It only traces for now, recording no trace in memory, and stays the root so that exar's experience can later assess and retain turns through its `on_trace`.
The debug chat is the root invocation `debug` the same way, around the adapter's `run_stream_native` (`server.routes.debug`), under a config that allows everything, since its agent only reads the conversation it analyzes, and a compaction asked by the user is the root invocation `compact` under the config of its deps.
The compaction summary, the case base prompts, and `llm.complete` are exar agents (`exar.pydantic_ai.PydanticAiAgent`) whose run function keeps every option of `Agent.run` native, invoked with `exar.agents.call`, so each nests below the run it is part of, such as the turn a compaction interrupts or the program whose `complete` calls it, and records nothing in memory.
The title and the debugger's `report` are plain pydantic-ai runs, as nothing reads a trace of them, the report's second turn continuing the first one's messages.
Usage is pydantic-ai's alone: the turn's `RunUsage` holds the main agent, its subagents, `llm.complete`, and every compaction summary, which runs on a `RunUsage` of its own for its request limit of one and is added to the turn's, so the turn's `UsageLimits` and the conversation's stored usage see it, and a compaction asked by the user adds its summary to the conversation's usage directly.

### The debugging consoles are off by default

The debug routes exist only when the operator sets `debug.enable`, otherwise the server never registers them, and `GET /settings` reports `debug` so the SPA hides the consoles.
They serve admins only.
`/api/v1/debug/conversations/{id}/chat` lets an admin ask the `debug` agent about one of their own conversations, found by the same `owned_conversation` check every conversation route uses, so no route reads across users.
The route is stateless, the client sends the whole debugging session with every request, so nothing about it is stored.
`agents/debugger.py` flattens the conversation into numbered steps, one per message part, model response, changed instructions, and stored run error, which the agent reads overview first, then failures, single steps, and searches.
Neither pydantic-ai nor the Vercel AI protocol records durations, so a step's time is derived from the stored timestamps: a model response from its request being sent to its stream starting, a tool return from its call's response starting to the return, generation included.

The `report` agent condenses a session into Markdown with the conversation's data replaced by typed placeholders.
`LeakCheck` then holds the report against the conversation word by word, flagging the words of the conversation that hold digits or address characters, are capitalized mid-sentence, or are unknown to the English and German dictionaries of `pyspellchecker`, which keep only common words, unless Hivegent's own tool schemas and instructions use them.
A quote of common words alone identifies nothing, so quotes leak through these words too.
A report that leaks gets one more turn to fix it, and what it still leaks is masked as `<REDACTED_n>`.

### Export, citations, and attachments

A `ConversationArchive` has two halves: `backend`, the persisted active path plus the instructions each turn actually ran under (read off `ModelRequest.instructions`), and `frontend`, what the tab held, including a turn that errored before it was persisted.
Import restores `backend` when it has messages and falls back to `frontend`, and archived prompts are never replayed.
`load_archived` closes a call the archive still awaited, since no stored requests remain to resume it.
An import is held to what a run may store (`accept_imported`): no message carries more images than `multimodal.max_images`, each image is held to the attachment cap, a tool result is cut to `llm.tool_output_max_chars`, and its data chunks never load, since pydantic-ai's loader drops every data part.

Citation line chips only show evidence captured in persisted read, grep, or search outputs, kept apart by tool call id, and a chip without supporting output is hidden.
Opening the document never applies a historical line anchor to current content.

A chat turn attaches only images in `formats.INGESTIBLE_IMAGE_MEDIA_TYPES`, so nothing converts on the chat's latency path, and anything else belongs in a workspace, which indexes it once instead of re-sending it every turn.
`/settings` serves the same table and size cap as `AttachmentLimits`.

### RAGold entries are built from what the turn stored

`POST /conversations/{id}/ragold` turns one edited turn into a RAGold zip (schema version 3): `annotations.json` with one entry and every listed document under `files/<documentId>/<name>`.
The client sends canonical workspace paths, which resolve with the caller's access like any document route, and ships the original file, or the markdown of a markdown-only entry (`find_source`).
Passage text comes from the markdown projection, so it carries no `location`.
The author is the user's name, falling back to their ID, and the project is `settings.ragold.project`.
The entry is complete when it leaves hivegent: question type, answerability, and every passage grade are picked by the user without defaults, and key facts are marked in the response, which `RagoldRequest` checks to be verbatim excerpts.
The archive models live in `ragold`, whose `load` also reads such an archive as a pydantic-evals dataset scored by exar's `KeyFacts` and `AnswerabilityJudge` (`exar.eval.evaluators`).
Since RAGold matches passages against the rendered original, `RagoldPassage` turns their markdown into plain text (`plain_text`), one block per line with table cells joined by tabs, and refuses a passage of markdown syntax alone with a 422.
The text still differs from RAGold's rendering in whitespace and line breaks, which RAGold and its evaluators tolerate by normalizing both sides as its README specifies.
The zip is spooled in full, hashing each file while it is copied, before the first byte is sent, so a failure is an HTTP error rather than a truncated archive.
`schemas/ragold.json` at the repo root is RAGold's published schema, which the contract test validates against, so refresh it when RAGold changes the format.
The route and the client action are hidden while `settings.ragold.url` is unset.

## Skills

### A skill library is a root of its own

Agent Skills (agentskills.io) are folders with a `SKILL.md`, which `shared/skills.py` parses: frontmatter keys beyond the specification are warnings, `name` must equal the folder, and `allowed-tools` is never read, since nothing a skill declares may widen what a run does.
The frontmatter is read without YAML's implicit types, so `version: 1.0` or `on: yes` stay text, and a repeated key is refused rather than one value silently winning.
Of Claude Code's keys it reads `disable-model-invocation` and `user-invocable`, flags spelled only `true` or `false`.
A skill is not a document, so it never lives in a workspace, where it would be indexed, cited, and converted file by file, but in a root beside `/tmp` (`skills.py`).
A user's and a group's library are `data/skills/<owner>/`, a `workspace.operations.Root` without store addressed `/skills/~` and `/skills/@<group>`, whose policy is `Reviewed`, so every agent write asks in every mode, the approval shows the diff, and a group library is writable only with the group's write grant, exactly where its workspace is.
The global skills, `/skills/srv/<name>`, are read in place from `skills.dir` and the plugins' skill directories, each skill a read-only root of its own, never copied into `data/` and never among the writable roots, so the agent cannot author one by construction.
`UserDeps.search_paths` adds every skill root, so the read tools and the sandbox mount reach skill files with no code of their own, and reconcile, indexing, and retrieval never see them.

### Names resolve by scope

`skills.visible_skills` lists the skills of `User.scopes` in their order, user, groups, global, and a name is unique only within its scope.
A skill is named by its reference (`libraries.library_ref`), its scope's prefix and its name as a document path spells them, `/srv/<name>`, `@<group>/<name>`, or `~/<name>`, which the catalog lists, `load_skill` and an assistant's `skills` take, and a prompt invokes by `/<reference>`, a global one's leading slash dropped.
A bare name names the skill of the narrowest scope that has it, the user's own before a group's before a global one, and none while two groups have it (`skills.resolve_skill`), so no saver can make a skill of a wider audience stand in for the user's own.
`PUT /api/v1/skills/{~|@<group>}` installs one ZIP archive, the skill folder or its content, validated whole (paths sanitized, OS junk dropped, `skills.max_files` and `skills.max_bytes` checked against the sizes the archive declares before anything is inflated) and swapped in with one rename under the library's lock, replacing a skill of its name, within the library's `skills.library_max_bytes` as an agent write is.
Its scripts are checked in the sandbox pool, as every listing checks them, by at most half its workers at once (`check_skill_script`), so neither an upload nor a load starves `run_python`.
`GET /api/v1/skills` lists every skill the user sees with its reference, problem, warnings, whether the model may load it and a prompt invoke it, and whether the user may change it, `DELETE /api/v1/skills/{scope}/{name}` deletes one, and `GET /api/v1/documents/{path}` reads any file of a skill the user sees.
A user's reset and `hivegent reset {user,group}` remove the owner's library, and `hivegent reset factory` every library.

### The catalog is a tool result

Most skills are written by users, so no skill reaches the model as an instruction, the operator's included, which keeps one channel.
`SKILLS_NOTE` (`agents/tools/skills.py`) renders the catalog before each model request and reports it as a synthetic `load_skill` call without a name, as `MEMORY_NOTE` reports the memory, wherever the window shows another catalog or none, so a change appends and the cached prefix stays.
`load_skill(name)` returns the body and a capped listing of the skill's files, each script marked runnable with its `main` signature or reference-only with the first diagnostic, fenced like every result.

The host loads a skill the model did not ask for by the same synthetic call, hidden from the transcript, wherever the window has not loaded it yet: one the run's assistant names and every skill the window before the latest compaction loaded, so a summary never drops the instructions the conversation works by.
A skill the latest prompt invokes by starting with `/<reference> <text>` or `/<name> <text>` is loaded with every `$ARGUMENTS` of its body replaced by the text, and again whenever that changes its result, since the note skips a result the window already shows.
`disable-model-invocation: true` leaves a skill out of the catalog and has `load_skill` refuse it, while a prompt and an assistant still load it, and `user-invocable: false` keeps a prompt from invoking it and the composer from suggesting it, while the model and an assistant still load it.
A library change, an install, a delete, or an approved agent write through the folder's `on_commit` hook, is announced as a `library-changed` feed event to every client following the library's scope.

## Assistants

### An assistant only narrows a run

An assistant (`types.AssistantSpec`) is a description, instructions, skills to load, tools to withhold, documents to focus on, to hide, and to reach only, and the highest mode a run with it may take.
A user's preferences name one by its reference in `assistant`, such as `~/legal`, which `build_run_prefix` resolves exactly for the user, so no assistant of another scope stands in for it, and one they no longer see applies none, since a stored choice outlives what it names.
`assistants.apply_assistant` narrows the run with it before anything else reads it: the withheld tools are joined, the mode is capped by `MODE_ORDER`, and the document lists are extended.

The preferences, the assistant, mode, chat model, and reasoning effort (`types.Preferences`), live in `users.preferences`, only the fields a user set, so a default the operator changes reaches everyone who never chose, and `PATCH /preferences` merges the fields it sends, so two clients changing different ones never undo each other.
A stored value that no longer validates, such as a model the catalog dropped, reads as its default (`Preferences.stored`), and a chat request is the AI SDK's body alone, so every client's turns run alike.
A user message names the documents it is about in its metadata (`types.DocumentSelection` under `documents`), which is stored with it, so a regenerate, a resumed turn, and a compaction run with the selection of the question they answer (`DocumentSelection.of`).
Its `allowed_documents` never enter the request, so only a selected assistant confines a run to them (see Scope and filters).
Nothing an assistant holds can widen what the request already allows, so a client cannot gain a tool, a mode, or a document by naming one.

### Assistants are files by scope

The global assistants are `<name>.json` files in `assistants.dir`, which the boot parses with every tool they withhold checked like `tools.disabled`.
A group's and a user's are `data/assistants/<owner>/<name>.json`, written only by `PUT` and `DELETE /api/v1/assistants/{~|@<group>}/{name}`, a group's only with its write grant, and lie outside every root a tool reads or writes, so no agent can author or change one.
`GET /api/v1/assistants` lists them user, groups, global, each with its reference, and a name is unique only within its scope, as a skill's is.
A user's reset and `hivegent reset {user,group}` remove the owner's assistants, and `hivegent reset factory` all of them.

### Only the operator's instructions are instructions

The operator's assistant joins the run's instructions as `ASSISTANT_NOTE`, an appending instruction like the mode note.
A group's or a user's assistant was written by people the operator did not vet, so its instructions reach the model only as the result of `read_assistant`, fenced and attributed to its author like every result, which `ASSISTANT_NOTE` reports wherever the window shows another selection, appending to the history so the cached prefix stays.

## Case bases

### A case base is a file or a folder

A CSV, XLSX, Parquet, JSON array, or JSON Lines file, a folder of JSON files keyed by their path in it, or graphs as BPMN or cbrkit graph JSON, one file or a folder, is a case base (`tools.cases`), detected by cbrkit's `io.detect` and loaded by its `io.load` from a snapshot of the files the caller may see, so a folder leaves out the sidecars, hidden files, and every member the caller's filter hides.
No state lives anywhere but in the files, which are the cases.
The configuration is what cbrkit's `spec.infer` decides from the data in memory, every decision with its provenance and reason, deterministic from the data, so a query ranks the same way as long as the data is the same.
Records are compared by cbrkit's inferred attribute-value measure, graphs by `LAP` with node measures per element type.
A sidecar beside the file or folder (cbrkit's `io.companion(path, "sidecar")`) is neither read nor written while the format of cbrkit's specifications still evolves, so no configuration is persisted that a later format could not read.

### Models are bound by name

A configuration names its models, and the host binds them explicitly (`tools.cases.Bindings`, `cases.bindings`): the embedder `hivegent` is the embedding model of the search index, which an inferred configuration compares long texts with, and the language model `hivegent` is the chat model of the turn, the default one for a request outside a turn, bound only when one is configured.
cbrkit binds strictly, so a configuration naming any other model, or the language model without a chat model, is refused with `Unbound`, which the tool and the routes report as a correctable error naming both names.
The chat model is an async cbrkit language model, so it runs on the request's event loop, its prompts at most `llm.case_concurrency` at once (`exar.helpers.bounded_gather`).

### Loaded case bases are cached by their stats

`cases.CASE_BASES`, a `tools.cases.CaseBaseCache` of eight entries shared by the tool and the routes, keeps the last loaded case bases by their location, the `ContentStat` of each file by name, and the bound embedder.
Every call only stats the files and reads them on a miss or to plan a change, so any change to the data loads it anew, and the least recently used entry is dropped.

### Companions follow their subject by construction

`tools.entries.Part` is the one relation between a subject, a stem with the suffix of its original, and every file that belongs to it: the description `<stem>.md`, the original, the `.assets` payload, and the cbrkit companions of a case base (sidecar, cache, and lock, cbrkit's `io.companion` and `io.subject`).
`Part.path` derives each companion from the subject and `Part.of` is its inverse, naming the subject and kind of any path, so no seam spells a suffix or matches paths itself.
`EntryPaths` is a subject with the kinds it has, and every unit the gateway moves, deletes, or lands is one, a directory or plain file being a subject whose original is itself (`EntryPaths.tree`), so relocating a unit only changes its stem and every companion follows.
A cbrkit companion is no document, the tree and the listings hide them, and a write, an upload, or a standalone move of one is refused, while a delete may take one alone, such as a leftover sidecar, an upload over the original keeps them, and one of another suffix takes them with the entry it supersedes.
A collection upload never imports a companion, and a source sync never mirrors one, so a sidecar mirrored before is lost and deleted by the next sync like any file the mirror no longer admits.
Whether a file or folder may be a case base is cbrkit's `io.is_casebase`, which the tree reports as `DirectoryEntry.case_base_candidate` and the chunks of a document as `DocumentChunksResponse.case_base_candidate`, so the client offers the Similarity view exactly where the tool reads one.

### Reads never write

`similar_cases`, `GET /case-bases/{path}`, and `POST /case-bases/query/{path}` write nothing, the configuration is inferred in memory, and no route changes it.

### One tool ranks by example

`similar_cases` (effect `read`) takes the path of a case base and one example, a partial record, a graph file (`query_path`), an inline graph, or the key of one of its cases, it returns the ranked cases with cbrkit's `sim.explain`: per attribute its similarity, weight, contribution, and both values, for graphs the mapping of the query's nodes with coverage and whether it is exact, serialized by cbrkit's `model_dump(mode="json")`.
Without an example it returns the configuration, decision by decision, and the attributes a query may name.
Every file resolves through the search paths, so the document filters and an assistant's allowlist hold, and `tools.cases.Limits` caps the size on disk (`limits.max_file_size_bytes`) and the cases, and tightens the configuration's `limits` (`max_limit` for the hits, `max_node_pairs` for a graph query), which cbrkit's `System` enforces.

### Retain and maintain land as a changeset

`retain_cases` stores accepted solutions of a problem as the `retain` section of the configuration says, and `maintain_case_base` merges duplicates and removes cases the `maintain` section finds unneeded, both effect `write` in the `cases` feature.
The inferred configuration retains every solution in a writable case base but has no `maintain` section, so maintenance plans no change until configurations persist again.
Each plans with cbrkit's `System.retain` or `System.maintain` as a dry run over the cases in memory, and `io.render` turns the planned `Changes` into the bytes of every file they change in the format of the case base, which land as writes and deletes, each with the stat the file had when it was read, so a change made since is refused on commit.
The changeset lands like a `run_python` program's (`agents.tools.write.program_writes`): at once where no root asks, else staged for `apply_changes`, whose approval shows the diff of every changed file.
The changeset carries cbrkit's reason for every planned change (`tools.changes.ChangeReason`, its case key, action, and reason), which the stored changeset keeps and `Gateway.plan` copies into the `ChangesetSummary` the approval shows.
Both default to a dry run that only returns cbrkit's plan, every change with its rule and reason, CSV, JSON, and JSON Lines files and folders of JSON files can store cases, and a maintenance run compares at most `Limits.max_maintained` cases pairwise.

## Plugins

### Plugins are packages the operator enables

A plugin is a Python package with an entry point in the `hivegent.plugins` group, named for the plugin, whose object is a `PluginFactory` (`plugins.api`).
Nothing loads unless `plugins.enabled` names it, and `plugins.loader.plugins` builds each one on first use, which the registries make at import, so an enabled plugin that is missing, ambiguous, fails to build, or contributes an id already taken stops the boot with its name.
Plugins are code an admin installed and run in-process like any dependency, while third-party tools nobody vetted belong to an admin MCP catalog instead.
The Nix build (`plugins` in `default.nix`) builds each plugin project with pyproject.nix into the backend's uv2nix virtualenv rather than onto `PYTHONPATH`, so it runs on the backend's interpreter, and a dependency the lock lacks or holds at a refused version, or a module clashing with another package, fails the build instead of the boot.

### The facade is the only surface

A plugin imports `plugins.api` alone of the app, checked by `tests/unit/test_self_contained.py`, which exports only Hivegent's own names: the pipeline contract of `shared.pipeline_registry` (`Pipeline`, `ConverterPipeline`, `ChunkerPipeline`), the connector types of `shared.connectors` (`Connector`, `RemoteItem`, `Delta`), `ToolFunc`, and `Localized`.
It imports exar and cbrkit directly at the versions Hivegent ships, such as `effect` and `Retry` from `exar.tools` or `Parsed`, `offloaded`, and `configure` from `exar.ingest`, since re-exporting them would only hide that an upgrade of either changes the plugin contract.
The factory receives a `Host`, which validates `plugins.config.<name>` with the Pydantic model the plugin passes, extra keys refused, and lends the trusted HTTP client, and the boot refuses options a plugin never reads.
A secret among the options is a credential file like any other, `hivegent.PLUGINS__CONFIG__<NAME>__<FIELD>`, read into a `SecretStr` field of the plugin's model.
Neither the settings' repr nor a refusal shows a value of the options.

### Contributions are held to the host's policy

A `Plugin` contributes features, converters, chunkers, connectors, and read-only skill directories, each a directory on disk of skill folders that the boot validates.
Every registry merges the built-in ids with every plugin's through `plugins.loader.merge`, which refuses an id taken twice and names both owners, so no contribution shadows another and none depends on the load order.
A feature holds plain tools, each declaring its effect with `effect`, which decides the modes offering it and its approvals as for a built-in tool, none writes through the changeset gateway, and every call is bounded by `llm.tool_timeout_seconds` (`agents.capabilities._plugin_feature`).
Plugin pipelines are built with the same host facts and operator options as the built-in ones, and their synchronous parsers and chunkers reach the worker processes by import, which the empty `plugins/__init__.py` keeps from loading every other plugin there.

## External MCP servers

### The catalog is operator configuration

`mcp_servers.<id>` declares each server in the notation of MCP client configs (`.mcp.json`, Claude Desktop), so a standard `mcpServers` entry works unchanged, `http` with its `url`, `headers`, and OAuth client credentials, `stdio` with its `command`, `args`, `env`, and `cwd`, and for both its groups, tool lists, trust, and timeouts (`config.integrations.McpServer`, a union tagged by `type`, or by `command` versus `url` without one, which refuses the fields of the other transport and `sse`).
FastMCP's own `StdioMCPServer` and `RemoteMCPServer` are not reused, since they allow unknown fields and carry proxy options hivegent does not honor.
Secrets come from credential files (`hivegent.MCP_SERVERS__<ID>__SECRET_HEADERS__<NAME>`, `...__OAUTH__CLIENT_SECRET`, `...__SECRET_ENV__<NAME>`), while plain `headers` and `env` sit in the configuration, so no request, user, or group can add a server or reach its secrets.
The id is a `CatalogId`, so it holds no underscore and `<id>_` prefixes exactly that server's tools, and `check_tool_settings` refuses an id prefixing a built-in tool, so no two tools of a run share a name.
An assistant's `disabled_tools` and `tools.disabled` accept any name a configured id prefixes, so a switch stays valid while its server is down.
Over HTTP only Streamable HTTP is spoken, an SSE URL is refused at boot, and the URL is an operator endpoint reached directly by a client confined to its origin, its OAuth issuer's, and the origins of the endpoints the issuer's own discovery metadata names once the flow fetched it, not through the egress proxy.
The headers reach the server alone, never the issuer.
A `stdio` server is a subprocess of the backend, started and ended with its connection (`StdioTransport` without `keep_alive`, so a restart after a failure spawns a fresh one), whose environment is the MCP client's minimal base (`PATH`, `HOME`, and a few more) plus the configured `env` and `secret_env`, never the service's own, which holds its secrets.
It runs with the backend's privileges and outside the egress confinement of the HTTP clients, which is why only an operator can declare one, and `forward_user` does not apply to it.
OAuth client credentials go through FastMCP's `ClientCredentialsOAuthProvider` pinned to `issuer` and bound to the server's URL, which caches the token for the connection's life.

### Trust decides the effects

`policy.annotated_effect` maps a trusted server's hints, `readOnlyHint` to `read`, neither `destructiveHint` nor `openWorldHint` to `write`, anything else, absent hints included, to `execute`.
An untrusted server's hints are ignored, so its every tool is `execute` unless `effects` declares another effect for it, which also overrides a trusted server's hints.
The effect rides on each tool definition, so `HOST_GATE` holds an extra toolset to `host_policy` tool by tool, hiding what it denies and asking before what it asks, exactly as for a built-in tool.
`groups` resolve through `User.scopes`: a server with groups is visible to their members, one without belongs to the global scope, the only one a service principal sees, which leaves the bridge the reading tools of global servers.

### One connection per server, failures stay with it

`agents.catalog.CATALOG` holds one `CatalogServer` per server, built at import like the features, around an `exar.mcp_client.McpClient`, and its lifespan (`agents.integrations.integrations_lifespan` runs every integration's) supervises a connection that keeps the `MCPToolset` entered, lists its tools, asks again every `refresh_seconds` (default five minutes) since not every server announces a change, connects again when the list changed, which clears pydantic-ai's cached list, and reconnects with a backoff doubling up to a minute after any failure.
A run never connects: the toolset of `McpClient` enters nothing, offers the listed tools while the server is up and none plus a one-line instruction while it is down, and turns a closed connection into a failed tool result (`ToolFailed`) that also triggers the reconnect, while pydantic-ai's own `ModelRetry` for a refused call stays.
Outputs pass the same fence and spill as every tool, and subagents and `run_python` are offered no catalog tools.
A server set to `forward_user` is called on a connection of each call's own, built by pydantic-ai's per-run `headers` function, whose requests carry `X-Hivegent-User` with the caller's token subject, or `auth.service_client_id` for the service principal, and nothing else about them, while the shared connection lists the tools without naming anyone.
A `trusted` server's instructions join the run's as operator text, appended as a change when a later connection brings others, while an untrusted server's never reach the model.
`GET /tools` lists the connected tools of the servers the user sees, grouped by id, and `GET /admin/integrations` lists every server with its host, groups, trust, status, last error, and each tool's effect, never a URL path, header, or secret.

## Databases and APIs

`databases.<id>` and `apis.<id>` declare the SQL databases and HTTP APIs offered as tools, their secrets coming from credential files (`hivegent.DATABASES__<ID>__PASSWORD`, `hivegent.APIS__<ID>__SECRET_HEADERS__<NAME>`), so no request, user, or group can add one or reach its secrets.
Their ids are `CatalogId`s shared with `mcp_servers` (`Settings._check_tool_ids`), so `<id>_` prefixes exactly one source's tools, and `is_integration_tool` and `check_tool_settings` treat them like a server's.
`agents.external.SOURCES` builds each at import, an `exar.tools.sql.Sql` over an engine bound by name (`HostEngine`) or an `exar.tools.http.OpenApi` over the client bound as `HostClient`, so a URL without a driver, a statement whose declared parameters differ from those it binds, an unreadable document, or an unknown `operationId` stops the boot.
The URL of a database carries no password, which the settings refuse.
The model never writes SQL, so no string it sends can escape a read-only transaction: `Sql` reflects the allowlisted tables and compiles a selection with SQLAlchemy Core, every identifier checked against the reflected columns and every value bound, and the operator's statements take typed bound parameters alone.
Selections and read statements still run in a read-only transaction with a statement timeout as a second line, write statements commit, and the privileges of the account bound both.
The client of an API is `http_client.create_trusted_http_client`, confined to the origin of its URL like an MCP server's, carrying the configured headers, and `OpenApi` sends only the operations allowed, checking required parameters before it sends.
Each toolset is a `FunctionToolset` of `exar.pydantic_ai.toolset` with the effect of every tool as metadata, so `HOST_GATE` decides as for any tool: `GET` and `HEAD`, selections, and read statements read, any other method and a write statement write and ask, and a mode that does not write withholds the writes.
The tools carry no `ToolFlags`, as a server's tools carry none, so a service principal is offered the sources without `groups` (`User.scopes`) and a run confined to an assistant's documents is offered them as well, since they return no workspace content.
Their instructions list the source and its tools beside the operator's `instructions`, and `build_capabilities` composes each toolset as an extra, next to the catalog's, as `GET /tools` lists them.
Every server, database, and API is an `agents.integrations.Integration` of one registry, `INTEGRATIONS`, which `User.sees` filters by groups, and whose lifespan disposes an engine or closes a client.

## External sources

### Sources are operator configuration

`sources.<id>` declares each source with its connector, URL (none for `local`), extra hosts, Basic credentials, connector options, groups, interval, deletion brake, and conversion and chunking pipelines, the password coming from the credential file `hivegent.SOURCES__<ID>__PASSWORD`, so no request, user, or group can add a source or reach its secrets.
`sources.catalog.SOURCES` builds every connector at import, the built-in `webdav` and `local` and the ones enabled plugins contribute (`Plugin.connectors`, merged like pipelines), so an unknown connector, options it refuses or never reads, or a pipeline that is unknown or unavailable stops the boot.
Its pipelines convert every download, and its chunking is the default `chunks.chunk_and_index_document` takes for the mirror, so a reindex chunks it the same way.
A connector only lists and reads, since sources are read-only by design, and completeness is its job: a listing that misses what the source refused names the refused paths in `Delta.refused`, which a complete delta cannot hold, and raises only when it cannot list the source at all.
The WebDAV connector, as rclone does, names a folder, a response, or a file's properties the server refuses or never answers as refused rather than skipping them into a deletion, fails only when the root is refused or unreachable, completes a truncated (507) first `sync-collection` report by walking the collections, trusts only hrefs strictly below its root without dot segments, and lists each collection once, so a hostile server can neither escape the root nor make a sync loop, and it streams each file, refused once it passes its option `max_file_size_bytes`.
The `local` connector mirrors a directory the operator mounted at the OS level (SMB, NFS, sshfs, rclone, a host folder), so a source without a URL takes no hosts or credentials and `SourceHost.url` raises for it, which stops the boot of a connector that needs one.
It lists every file with `listed=True`, keyed by path and versioned by mtime and size, since inode numbers do not survive a remount on FUSE and CIFS, follows no symlink, skips anything but regular files and folders, reads each file through `exar.memory.mounts.open_beneath`, which opens every segment relative to the one above with `O_NOFOLLOW` and refuses anything but a regular file, so a file swapped for a symlink or a FIFO after the listing is refused, names unreadable ones in `Delta.refused`, and with `require_mount` (default) fails the listing unless the root is a mount point, so a dropped share is an error and not an empty folder.
A connector is a `shared.connectors.Connector`, `sources.webdav.WebDAV` for the built-in one, which its factory builds from a `SourceHost`, never a secret: its client (`security.create_confined_client`) carries the Basic auth and refuses every host but the URL's and `hosts`, follows no redirect, and honours no proxy variable, since a source is an operator endpoint that may lie on a private network the egress proxy refuses.
A confirm deletes held files only from a complete listing, and holds a file lost since, which the admin never saw, and a file moved onto the stem of a held one is held with it.

### A mirror is a workspace

A source's mirror is the workspace of `store.SourceScope`, keyed `source:<id>`, at `data/sources/<id>/` and mounted at `/mnt/<id>`, and its `workspaces` row names the source as its parent, so search, citations, previews, the tree, and reconciliation need nothing of their own.
`User.workspaces` adds every source whose audience the principal is in, its `groups` or the global scope when it has none, always under `Grant.READ`, which leaves the bridge the global sources to read.
So the lock and every row write refuse every change a principal asks of a mirror, from a tool or a route, and `WorkspaceInfo.writable` is false, so the client offers no upload, rename, delete, or rechunk there.
A mirror is `Reviewed`, and only the host's sync, which approves nothing, changes it.

### A sync is a diff landed as the host

`sources.sync.plan_sync` is a pure function with the mirror's rules: a file new or at another version is downloaded, one at another path under the same id is moved, which keeps its document and embeddings, and a hidden or reserved path, a cbrkit companion, or a stem another file holds is skipped.
A delta's `removed` ids, paths, and folders are tombstones, deleted at once and exempt from every brake, while a file merely missing from a complete delta is deleted at once too, as rclone does, since a mirror is a cache of its source and a wrong deletion costs only a download and its embeddings.
A listing with refused paths lands its downloads, moves, and tombstones but deletes no missing file, keeps the cursor so the next sync lists the refused paths again, and records them as the source's error, which `GET /admin/sources` shows, as rclone refuses every deletion after a listing error.
A known file is lost when a complete listing lacks it, when a listing keeps it though the mirror no longer admits its path, or when it collides with another file's stem.
The brake applies to every lost file, the held ones from before included: deleting them all, or the mirror, or more than `max_sweep_share` of it and more than `SWEEP_FLOOR` files, or any file held already, holds them, so they stay mirrored and are marked `source_items.held` while the downloads and moves land and the cursor advances.
A held file the source lists again is released, the held count is `count(*) where held` and shows in `GET /admin/sources`, and `POST /admin/sources/{id}/sync?confirm_deletes=true` deletes every held file still lost after listing the source in full, the only forced full listing, since an outage or a share unmounted lists like a mass deletion.
Deletes and moves land in one changeset, downloads in batches of uploads of origin `synced` with the local stat as their basis, a failed batch landing again in halves until the failing files are found, which keeps the repeated commits and conversions few, all as the host, so the mirror is converted, chunked, and indexed like any upload and announced once per commit.
`sources` holds each source's cursor, last sync, error, and requested sync, and `source_items` its remote id to path and version and whether its deletion is held, written in the transaction of the changeset that lands the files (`apply_changeset(record=...)`) as array parameters, so the items never disagree with the mirror's rows and no number of files outgrows the driver's placeholders, and a sync with failed files or refused paths keeps its cursor so the next one tries them again.
A file the mirror lost behind the sync's back, its copy or its row gone, is downloaded again from a full listing (`sources.sync._drifted`), and the reconcile removes a mirrored file no item names.
The housekeeping, in the background of the boot and then every `conversations.sweep_interval_hours`, removes the partial file of an interrupted `atomic_write` anywhere in the data directory once it is an hour old, so a write in progress is spared and no boot waits on the walk (`workspace.sweep_partials`).
A remote path is mirrored at its Unicode NFC spelling and read at its own, and two that fold onto one are named in the source's error.
A changeset that committed but failed to index raises `workspace.IndexingFailed`, which the sync counts as landed, since the boot indexes it.
Both tables are rebuilt by a full sync, and a source leaving the configuration is pruned at boot with its documents and mirror before the reconcile (`sources.sync.prune_sources`), which would otherwise fail on a mirror no source owns, as every `hivegent reset` but that of one owner prunes them all with their mirrors.

### One sync per source

The server runs the loop of `sources_lifespan`, which starts a sync of each source that is not syncing and is requested or older than `interval_minutes` (`db.sources.due`), as a plain task outside the job manager's slots, so it never starves uploads, holding no connection.
The sync keeps its `done` of `total` files in memory (`sources.sync.progress`) and clears its `requested` at the end.
Every request counts in `request_seq`, which `Due` records, so the end clears only the request the sync answered, and one made while it ran stays for the next, but a plain one while a confirm is requested, which lists in full anyway.
`POST /admin/sources/{id}/sync` only sets `requested` (`sync`, or `confirm` with `?confirm_deletes=true`, which outranks a plain one) and answers `202`, so a confirm made during a sync is not lost, and it wakes the loop.
Progress reaches admins as a `source-changed` event of the feed, after which their client lists `GET /admin/sources` again, which shows each source's connector, host, groups, interval, last sync, error, requested sync, progress, held deletions, and file count, never a URL path or secret.
Each connector is built once at import and opens its client when it first asks for it, and `catalog.CLIENTS` closes them all.

## Single-process assumptions

One server process runs per database and data volume, which `db.engine.serving` enforces.
The workspace locks, the journals, the job and list feeds, the maintenance flag, the turn registry, and the rate-limit buckets live in the process, and the workspaces are folders of one local disk.
PgBouncer in transaction pooling mode is not supported, since the server holds a session-level advisory lock (`db.engine.serving`).

## Document tools

`tools` is self-contained: it imports itself, `shared`, and the `exar` library alone, the package of modules without app state (`connectors`, `formats`, `l10n`, `pipeline_registry`, `skills`, `text`, `zips`), which imports only itself, which `tests/unit/test_self_contained.py` checks on the AST of every module, while ingestion is the `exar.ingest` library.
So the contracts it shares live in it, the workspace layout in `tools.entries` and the changeset in `tools.changes`, and whatever needs the app is handed in: every tunable is a parameter of the factory `agents/tools/*` calls per run, and the binary reader takes the executor of its PDF, animation, and video decoders, which the app isolates (`media.ISOLATED`, an `exar.ingest.ProcessPool` with a fresh process per call and a timeout).
A tool is a plain typed async function with the conventions of `exar.tools`: a factory named after it (`edit_document(paths, commit=...)`) closes over its configuration and returns it, `effect` declares what it changes, and `Retry` refuses input the model can fix.
`grep`, `jq`, `list_documents`, `read_document`, and `read_binary_document` are classes of `exar.tools.files` and `exar.tools.media` (`Grep`, `Jq`, `Ls`, `Read`, `View`) instead of code here: `agents/tools/explore.py` builds them directly for each run with `ctx.deps.mounts` as their `mounts`, a function of the run that exar calls when a tool runs, so the tools read the workspaces of the run as they are then, and the `Mount` of each workspace is its `SearchPath.mount` (`source` the workspace folder, `target` its scope prefix, `implied` for the personal one, `exclude` the globs of the document filter, `hidden` the globs of `.assets` payloads and case base companions), while the canonical spelling on case-insensitive disks is `exar.memory.mounts.canonical`. The sandbox's `query_table` is built the same way over the mounts of its run. `explore.py` hands them only what is specific to the workspaces, the `refusal_hint` that names `list_documents`, `read_document`, and the sidecar, the `.assets` wording of hidden entries, the query tool a read names, the description of how `search` and the markdown descriptions relate, and the rule that drops an original whose description matches too.
Generic wording, the descriptions of arguments and the rendering of results, is exar's default and is never repeated here, so the tools that follow migrate the same way.
It returns a pydantic-ai `ToolReturn[T]` (`exar.tools.batch.tool_return`), whose `return_value` is the compact text the model reads, with any files after it, and whose `metadata` is the typed data a program and the client receive, so the two channels need no envelope of their own and no adapter.
`T` names that data, which pydantic-ai publishes as the return schema the sandbox declares, and `agents.guards.ConversationSpill` wraps it into the `data-tool-output` chunk stamped with the call's id (`agents.output.with_data_chunk`).
`agents.common.AgentTools` builds a feature's tools per run from the run's deps and serves them through `exar.pydantic_ai.toolset` with their effects as metadata, which `HOST_GATE` holds to `host_policy`, installing the per-call hooks a tool declares by wrapping it in `Hooked` (an argument validator, a `prepare` hook) and the typed `ToolFlags` of the host (gateway, people-only, derived, internal, seeing) as one metadata entry that `host_policy` and the guards read back with `flags_of`, while `static_tools` lists their definitions once, as a probe principal (`auth.PROBE`) in `write` mode is offered them, so nothing built for listing can be called.
`AgentTools.build` builds a run's tools for every surface, the agents and their subagents, the sandbox, MCP, and the debug console, and `HOST_GATE` holds them to the run's policy (`UserDeps.policy`).
The app's MCP endpoint serves the same tools through `mcp.adapter`.
`tools/__init__.py` re-exports nothing, so importing the layout loads no tool.

Read tools serve the markdown projection, which retrieval and citation anchors build on.
Tables (`formats.TABULAR_SUFFIXES`), JSON (`formats.JSON_SUFFIXES`), and vision media (`exar.ingest.VISION_MEDIA_TYPES`) each have one table that gates the specialised tool and makes `read_document` point at it.

### Read tools are list-first

A read tool takes a list of items, and a one-item list asks for one, so no tool has a singular twin.
`exar.tools.batch.serve_batch` runs `exar.tools.run_batch`, which deduplicates items, serves them in request order with bounded concurrency, and turns one item's `Retry` into its `ItemFailure`, refusing only when every item failed, and renders each under a `==> key <==` header within its `BatchShare` of the budgets.
Batching lets a call share work, so `search` embeds and ranks its queries in one batch and `query_table` loads each file once for every query.
`exar.pydantic_ai.LenientArguments` accepts a bare item where a list is declared and unquotes a string encoded twice, only in arguments that fail validation and without changing the schema.
`grep` and the `path` of `list_documents` stay single.

### Scope and filters

A selection's `included` documents are advisory and only named in the prompt, so a run can follow a reference out of the selection.
Only its `excluded` ones become a `DocumentFilter`, enforced by the path tools through the `exclude` globs of the mounts (`DocumentFilter.globs`, which names the files of each file entry exactly, listed from the folder of the entry when the mounts are built, so `report.v2.pdf` is no file of `report.pdf` for an allowlist and an exclude alike), by the write gateway through `SearchPath.filter_func`, and by retrieval in `db.documents.accessible_chunks`.
Every entry is a `types.FilterEntry`, a path with an explicit `directory` flag, so no path syntax tells a directory from a document.
The stores and their filters travel together as one `reach.Reach`, built from `UserDeps` (`UserDeps.reach`), or from a REST caller's `User` with no filters, and it is the only input of the path tools' mounts (`Reach.search_paths`), `retrieval.document_memory`, and `db.documents.accessible_*`, so no caller can drop an assistant's allowlist by forgetting an argument.
A selected assistant's `allowed_documents` join the same filter as its `allowed` entries, and `parse_document_scope` then gives every scope the user sees a filter, so a scope no entry names is hidden whole.
An allowed entry admits what it selects and the directories above it, so a listing still reaches it, while an excluded entry wins over it.
The prompt names the allowlist beside the hidden entries, and the parity test runs both halves against an in-memory SQLite database.
A confined run is offered no tool marked `derived`, the conversation and memory tools and with them the `conversations` subagent, since past conversations and the memory may quote any document.
The conversation's own history and `/tmp`, which hold what earlier turns read, stay the run's, as the model already holds that history.
The planner refuses a directory move that would carry an excluded entry (`PathFilter.hides_within`), with the run's filters for an agent change and the approver's current ones for a staged changeset, while the HTTP routes pass none.

Retrieval scopes a search with one subquery on `documents` (`db.documents.accessible_chunks`), passed to cbrkit as a SQLAlchemy predicate, which matches the caller's owners and compiles the hidden entries to SQL as `DocumentFilter` reads them.
The index itself is `Unscoped.TABLE`, which only indexing (`retrieval.index_document`) and re-embedding hold, and every read narrows it.
`retrieval.document_memory` narrows the chunk index, a `Pgvector` storage of `Chunk` rows, to that scope with cbrkit's `narrow`, which restricts its reads, its retrievers, and its writes alike, and opens `retrieval.chunk_memory` over it: an `exar.cbrkit.IndexMemory` of `Chunk` rows by chunk id, searched by the index itself and read through `get_where`, in an `exar.memory.GatedMemory` that denies every write, since chunks change with their documents.
So the `search` tool and any other consumer of an `exar.typing.Memory` search the same chunks, and `exar.cbrkit.RetrievalMetric` can score the hits by chunk id.
The queries of a `search` call are one memory search, which embeds them in one call and ranks them on one connection, over-fetching `rerank.candidate_multiplier` times the limit for the reranker.
It fuses each query's whole reranked pool by reciprocal rank before cutting to `max_results`, so a chunk several queries rank just below the limit still surfaces, then reads the found rows on one connection and their documents in one query.
The lifespan's `warm_index` loads a local embedding and rerank model before serving, and a model loads once however many searches start it, since cbrkit's `lazy` builds under a lock.
A remote embedder sends batches of `embedding.batch_size` texts, `embedding.concurrency` requests at a time, with `embedding.request` passed verbatim as pydantic-ai's `EmbeddingSettings`, and cuts texts client-side to the limit the model reports, which only OpenAI's own models do (see "A provider is named for its runtime").
Nothing is probed from the provider.
Queries and chunks are embedded behind `embedding.query_prefix` and `embedding.document_prefix` for either provider, and the queries as pydantic-ai's `query` input type.
A `search` call records a `vector.search` span with its search type, limit, query count, and result count.
The statement thus binds a parameter per store and filter entry, never one per document, and the scan never ranks a chunk the caller cannot see.
Every connection sets `hnsw.iterative_scan=strict_order` (`db.engine`), since an HNSW scan otherwise filters only its `ef_search` nearest chunks and returns fewer than asked once the caller sees a small share of them.

A filter argument naming a directory no accessible root holds is refused (exar's `Ls` and `Grep` raise a `Retry` once `exar.memory.mounts.narrow` and `Mount.canonical` find no mount holding it) rather than answered empty, which would read as an empty workspace.
The refusal names the caller's own spelling and the roots, without demanding a prefix.

### vLLM embeddings

vLLM truncates over-long inputs itself when asked through `extra_body`, and its embedding models expect their prefixes or instructions from the client:

```toml
[embedding]
provider = "openai"
endpoint = "vllm"
model = "intfloat/multilingual-e5-large"
dimension = 1024
query_prefix = "query: "
document_prefix = "passage: "

[embedding.request.extra_body]
truncate_prompt_tokens = -1
```

A Qwen3-Embedding style model embeds documents as they are and instructs its queries, such as `query_prefix = "Instruct: Given a question, retrieve passages that answer it\nQuery:"` with `document_prefix = ""` and `dimension = 1024` for `Qwen/Qwen3-Embedding-0.6B`.
`dimension` must match the model's output size, or `request.dimensions` for a model that shortens its vectors.

### Mutations

Every write, edit, move, delete, new directory, and upload is an item of one changeset committed by the one gateway in `workspace.changeset`, the HTTP routes included.
Operations are generic over the location type, `str` where tools and staged programs spell paths, and `workspace.Location` once `workspace.operations.route` sent them to their root.
Items apply at once, like a diff of two states: sources and bases name the workspace as it is, destinations and targets as it ends up, so chains, swaps, rotations, overwrites, and case-only renames fit in one changeset.

`plan_changeset` resolves sources into units (an entry with its description, original, and assets, or a directory tree) and checks the final state rather than an order: each unit is claimed once, each final path filled once, every destination is free or vacated, and a new original's stem holds no other entry.
So a move onto an occupied path needs a delete of it in the same changeset, a destination naming a directory that stays means into it like `mv`, and a write follows the move that carries its file.
It returns the `tools.changes.ChangesetSummary` an approval shows, built from the resolved effects.

`apply_changeset` converts uploads and converter-backed originals without a lock, takes every involved root's lock in `store_key` order, resolves again under them, and installs files through one rename journal (`paths._Journal`) and rows in one transaction, so a failure restores both.
Moved units are parked under `.changeset-park/` inside the transaction, which lets a swap pass the `(owner, stem_path)` unique constraint, and rows are updated rather than recreated, so a moved document keeps its id and embeddings.
Indexing runs before the locks are released, so no other change and no read sees an entry without its chunks, and a write to the same workspace waits for it, and the commit is announced once.

The journal names its staging area before anything is staged, appends the undo of every step to `<data_dir>/.journals/<stage>.jsonl` and flushes it before taking the step, flushes the directories of every rename, records every undo it made, and records the commit once those directories are flushed, and the staging area goes before the journal, so no staging area outlives a crash unnamed and the journal survives a power loss as it does a crash.
The journal names the row transaction's id (`pg_current_xact_id`) from inside it before it commits, so a crash or a lost acknowledgement between the rows' commit and the journal's is settled by `pg_xact_status` (`db.changes`): the files are kept when the rows committed and rolled back otherwise, with no table of its own.
One server runs per database and data directory, which it holds as a session advisory lock for as long as it serves, refusing a second one and any host operation (`db.engine.serving`).
So the boot, before the migrations, rolls back every commit a crash interrupted, keeps one whose rows committed, and resumes a rollback that stopped (`workspace.recover_changesets`), and empties the transient folder (`files.reset_transient`, `<data_dir>/.transient`), the temporary directory of the server and its workers, below which every temporary file lies: the spooled uploads, the staged skills, and the temporary directories of conversions.
A server checks every 30 seconds that the connection holding its lock is alive (`db.engine._watch`) and stops with `SIGTERM` once it is not, since it would serve on unlocked.
Its supervisor brings it back, since the systemd unit restarts it `always` and backs off to a restart a minute, and dinit restarts it with `restart = yes` at least 10 seconds apart, both below their start limits, so a database outage of any length ends in a fresh server, while an operator's stop keeps it down.
The lock connections come from an unpooled engine of their own (`db.engine._get_lock_engine`), so they never take a slot of the pool that `db.pool_size`, `db.max_overflow`, and `db.pool_timeout_seconds` size.
The source mirrors' pruning skips hidden names, so it never takes the staging area of a change, and the boot removes a staging area beside the data directory's roots that no journal names, which only a crash leaves.
Every undo is idempotent and an undo whose record cannot be written is merely replayed, so a retry never fails on what an earlier attempt did.
A rollback stops at the first step that fails and keeps the staging area and the journal, marking the journal kept and logging both, since the staging area holds the parked files, and an undo never replaces a path that changed since its step.
While any change is kept or any journal cannot be read (`workspace.kept_changes`), reconciliation, at boot or by `POST /admin/reindex`, is refused with a `409`, since it would take the parked files for deleted and drop their rows.
Every file the server keeps is written through `cbrkit.io.atomic_write`, a flushed sibling renamed over it, so neither a crash nor a power loss leaves a partial one: a file a change stages, a skill, an assistant, and a staged program changeset.

An upload is a `workspace.Upload` item only the host builds, so a stored `Changeset[str]` never carries bytes.
An `overwrite` replaces the entry at its stem under the gateway's locks and keeps its origin, in place when the upload consists of the entry's files and by deleting the entry first otherwise.
`replace_original` and `reconvert` are uploads over the entry, and a change made while converting refuses the commit with a 409.
An upload's `modified_at` (the browser's `File.lastModified` on an upload or a replaced original, a source's last modification on sync, a ZIP member's date, or the original's own mtime on reconvert) dates every file the entry lands, so the mtime `list_documents` reports is the content's date, not the last conversion's, and a text edit dates the entry now.
It must name its zone, which the routes enforce as `AwareDatetime`.
A ZIP member's date is its extended timestamp (`shared.zips.member_mtime`), else its DOS date read as UTC, the zone the browser's JSZip writes, and the RAGold export writes the extended timestamp beside the DOS date in the server's local time.
An index failure leaves the files with a null digest for reconciliation.

An asset description `<stem>.assets/<name>.md` is a unit of its own that only the host writes or deletes, refused unless its asset stays, and `workspace.generate_asset_description` runs the vision model before the commit takes the lock.

A `workspace.operations.Root` is a `SearchPath` with the rules its commit follows (`quota`, `store`, `on_commit`), so the planner and executor never ask which kind they hold, and a workspace's is `Root.of(store, filter)`, its filter refusing a directory move that carries hidden entries.
A root without store, such as `/tmp` or a skill library, skips rows, projections, indexing, and announcements, and is left out of the summary and the `WorkspaceChanged`.
Only the host (`Gateway.host`) may write what a root reserves or let its quota evict.
Root locks come from one registry by `store_key` (`locks.root_lock`), and a commit stages beside every root it changes (`_staging_root`), so every step stays a rename.

`POST /api/v1/changes` applies a list of operations (`server.models.ChangesRequest`) as one changeset within the request, and the single-item routes (`POST /documents/move/{path}`, `DELETE /documents/{path}`, the `/directories` routes) build one such operation through the same `_apply`.
A delete must name the kind it `expect`s, so a client never removes a folder by accident.
Each route answers with the applied `WorkspaceChanged`, since a move into an existing folder lands below it, under the id the feed announces it with, so the caller applies it at once, even while its feed reconnects, and skips the announcement.
Every one of them spends the upload rate limit.
Uploads, writes, asset descriptions, rechunk, reconvert, and wiping a scope keep their own routes.

Paths compare as the workspace's filesystem compares them: on a case-insensitive one (`tools.entries.folds_case`) claims compare by `tools.entries.path_key` and existing paths are respelled as on disk (`tools.entries.respell`), so only a destination's last segment keeps its spelling.
The tools respell through `Mount.canonical` (`exar.memory.mounts.canonical`) before any filter is asked, so `SECRET.md` cannot reach a hidden `secret.md`.
A basis is a content hash or a `tools.entries.ContentStat`, which includes the inode, since a same-size replacement with a preserved mtime is the one change `(mtime, size)` misses.

The agent's mutation tools are list-first (`moves`, `paths`, `edits`) except `write_document`, and no argument is a glob, so the user approves exactly the paths that change.
`workspace.Gateway` binds the roots a surface may write, its filters, and the user it tells.
Each validator in `agents/tools/write.py` goes through `_gate`, which plans the changeset when an item routes to a workspace and raises one `ApprovalRequired` carrying the `ChangesetSummary` with canonical paths.
The commit is one changeset over the run's roots, so a batch spanning a workspace and `/tmp` lands whole or not at all.

### The image cap belongs to the gateway, not to the reader

`multimodal.max_images` is the serving gateway's per-request image limit (vLLM's `--limit-mm-per-prompt`), which rejects the whole request rather than the call that overfilled it.
A catalog model overrides it with its own `llm.models.<id>.max_images`, resolved into `LlmConfig.max_images`, and every consumer reads the cap of the model it calls, `agents.guards.max_images` for a run.
`ReadBinaryDocumentTool` binds `max_pages` (refused, naming `pages=`) and `max_frames` (clamped) to it, and a batch divides the cap among the files that become images.
`exar.pydantic_ai.PromptImageLimit` (`agents.guards.PROMPT_IMAGE_LIMIT`, with localized notes and the cap resolved per request from the run's model), carried by every agent in `agents/app.py`, replaces all but the newest `max_images` images in the outgoing request with a note, since parallel reads, attachments, and replayed history all fill one request.
It edits only the wire copy in `wrap_model_request`, never the run's history, which `before_model_request` would rewrite, and rebuilds what it touches, since parts are shared with that history.
`ConversationSpill` is the deliberate exception that replaces the recorded return.
Vision calls of the upload pipeline run on the tool-free `llm.complete` agent, so `workspace.describe` clamps its sampled frames to the aux model's cap itself.
`_accept_attachments` refuses an over-cap attachment set at send time, served to the composer as `ModelInfo.max_images` of each model.

### Tables are queried, not read

`query_table` runs DuckDB SQL against the original CSV, TSV, Parquet, or spreadsheet file, since a row-per-line projection wastes context and clips trailing columns.
Polars loads and types the files, and DuckDB runs every query, the schema call included, on a throwaway in-memory database with file access, extensions, settings, and Python variables locked out, so a query reaches nothing but the registered frames by construction, and a timeout interrupts it.
Files are addressed positionally as `t`, `t2`, `t3`, so no path is interpolated into SQL, every file is registered so `SHOW TABLES` works, and omitting the query returns the first table's schema.
The dialect is named DuckDB SQL and anchored to PostgreSQL, so one word sets the model's priors on quoting, casts, and function names, and DuckDB's own errors already point at the spot in the query and suggest the names it meant.

Every text column whose values all parse as a number, date, datetime, or boolean is retyped before the query, and a single zero-padded value marks an identifier and vetoes numeric typing.
A column that falls short is reported as a `TextColumn` with the parsed share and a sample of unparsed values, scoped to the columns the query touched and led by the worst shortfall.
The tool repairs nothing and asks instead, since a spilled header row, a `<100`, or a `0,05` each need a decision about someone else's data.
When most mixed columns hold their unparsed values only in the first rows, those rows read as a title, group, or units row, and a `HeaderGuess` names the `skip_above_header` and `skip_below_header` that would drop them, in the hints, the failures, and the data a program receives.
Both are applied to the loaded frame rather than by the readers, so a spreadsheet and a delimited file count rows alike, as the sample shows them.
Column names that are not bare identifiers are listed double-quoted (`_quoted`), and a parser error states that rule only when the query spells such a column unquoted.
A parser error also shows the query line with a caret at the position sqlparser reports, since the token it names is rarely the mistake itself.

Excel is read through `fastexcel`, and a delimited file in a legacy encoding is retried once decoded on a `ComputeError`.
The row limit is the only cut that reaches the data, and a table too long to show whole is spilled.

### JSON is filtered, not read

`jq` is `exar.tools.files.Jq` over the workspaces, which needs the `jq` binary and fails when the tool is built without it.
It runs a filter against the original `.json` file, and omitting the filter returns the top-level keys and their types.
`.jsonl` is left out, since jq is handed one document.
The text is passed as read, so a malformed file is reported in jq's own words as a `Retry`, and every value renders as one compact JSON line.

### Registered or excluded, never deferred

A tool is either in the model's initial context or not registered for the run, and there is no `defer_loading`.
A model whose tool list lacks a name works around a pointer to it rather than searching for it, and `OpenAIChatModel` has no wire-level deferral.
`settings.tools.disabled` is the operator's list of tool names, unioned with the assistant's `disabled_tools` into `UserDeps.withheld_tools`.
Offering is decided by the deps alone, `UserDeps.policy` (the `host_policy` of the run): a withheld name, a people-only tool for a service principal, and a tool marked `derived` (the conversation and memory tools) while an assistant's allowlist confines the run.
`HOST_GATE` applies it to every agent's tools, `OFFERED_TOOLS` hides the `sandbox_only` ones from the model, and `listed` does so for MCP, so the main agent, every subagent, the extra toolsets, the sandbox, MCP, and the debug console hold to one rule, and `build_capabilities` drops a feature none of whose tools is offered, with its instruction block.
The lists also take the delegation tools and the subagent names: withholding `delegate_task` drops `SubAgents`, and a subagent is dropped when its name or every one of its tools is withheld.
Both lists are held to `capabilities.KNOWN_TOOL_NAMES`, the operator's at startup and a request's with a 422 problem, since a misspelt name withholds nothing.
An internal toolset (`AgentTools.internal`), such as `assistant`'s, serves the host, so no list withholds it and its tools are listed by `GET /tools` without a switch.
`GET /tools` lists `capabilities.tool_infos`, the canonical catalog of every name a chat model may call, the feature tools, the delegation tools and subagents in the group `subagents`, and the readers of saved results (`guards.SPILL_TOOLS`), each marked `switchable` when a list takes it.
It defaults to empty, and `check_tool_settings` refuses an entry that matches nothing at startup.
`query_hint` keys its pointer to `query_table` on the entry rather than the requested path, since an uploaded table is served as its `<stem>.md`.

### The MCP surface serves the agent's tools

Setting `mcp.base_url` mounts the endpoint, and the boot refuses it without `mcp.confirmation_key` and `proxy` mode without its `mcp.client_id` and `mcp.client_secret`.
`mcp.base_url` is the endpoint's public URL, whose path must be `/mcp`, and the resource its tokens name, which FastMCP advertises exactly, without the trailing slash a mount at `/mcp` would add.
FastMCP's app is built with the path `/mcp` and served at the root of the API's app (`mcp.app.McpApp`): the endpoint at `/mcp`, the discovery documents RFC 9728 and RFC 8414 place at the root, `/.well-known/oauth-protected-resource/mcp` and in `proxy` mode `/.well-known/oauth-authorization-server/mcp`, which Caddy forwards too, and the OAuth routes of the proxy below `/mcp`, where its issuer and `base_url` place them, its callback at `/mcp/auth/callback`.
So the `WWW-Authenticate` challenge of an unauthenticated call names a metadata document that is reachable and names the resource the audience is checked against.
MCP serves the `explore` and `write` features and every plugin feature (`capabilities.PLUGIN_FEATURE_IDS`) through one adapter (`mcp/adapter.py`), so a client calls exactly what a model calls, schema, validator, and gate included, and a plugin tool whose effect is not `read` asks before every call there too.
The server is `exar.mcp.server` over the factory `adapter.agent_tools`, which builds the tools of each request for the user the token names (`token_user`, or the development user under `hivegent dev`), so listing the tools resolves the caller too.
The other features need a conversation or a sandbox, and each request lists and calls only what the caller's deps offer, so `tools.disabled` withholds a tool here as from a run, while `tools.sandbox_only` only places a tool in a chat turn's sandbox and leaves it served here.
Each request builds the caller's `UserDeps` in `interactive` mode with no conversation, and the served set is settled once when the app is built by each tool's `prepare` hook, which withholds `apply_changes`.
Subagents need a chat turn, so MCP serves none.
Each tool's annotations derive from its effect (`exar.mcp.annotations`).
The gate's `ApprovalRequired` is answered over exar's SEP-2322 round trip (`exar.mcp.Confirmations`) with the `ChangesetSummary` and its diffs as the description, and its request state is an HMAC over the caller, tool, arguments, description, a nonce, and an expiry, so a client can neither forge it nor carry an answer to other arguments or a changed workspace.
The key is the secret `mcp.confirmation_key`, so an answer holds across a restart.
Each answer runs its call once: its nonce is spent in the `mcp_confirmations` table (`db.confirmations.PostgresNonces`, `INSERT ... ON CONFLICT DO NOTHING`, swept once expired), kept across restarts, so a replayed answer asks again rather than running a write or a plugin's `execute` tool twice.
The approved retry carries the digest of the summary shown (`approval.APPROVED_CHANGES`), and the write gate plans the changeset again and refuses the call if it is no longer the one approved.
Every token must name this endpoint as audience: in `proxy` mode the upstream token names `mcp.client_id`, the proxy's own client, and in `remote` mode, where clients register dynamically under their own ids, it names `mcp.base_url`, the RFC 8707 resource, or `mcp.client_id` when set.
The API's `auth.audience` lists exact client ids alike, since a pattern would admit any client an IdP admin later names to match.
A return is its text bounded by `exar.text.truncate_middle` at `settings.mcp.output_max_chars` (`None` sends it whole) and its binary attachments as image or resource blocks, with no `structured_content` or `outputSchema`, since major clients hand structured content to their model in place of the LLM-tuned text or beside it.

### Tool results are fenced as data

`agents.guards.TOOL_OUTPUT_FENCE`, the `exar.pydantic_ai.ToolOutputFence` of Hivegent, wraps the text of every tool result in `<hivegent_untrusted_data>` tags on the wire, and its instructions tell the model that what is inside carries no authority: it acts on instructions there only where the user asked for that and the step is low risk, the graduated handling of OpenAI's Model Spec, so a document the user wants applied still is, while nothing inside may trigger a tool call, a link, or a change the user did not ask for.
The tag is one descriptive name, as Anthropic's prompting guide advises, prefixed with the product so no document carries it by accident, and the result sits in XML, as OpenAI's Model Spec advises for untrusted data, rather than behind a random boundary per result, which would cost tokens on every result and vary the context for nothing.
What a random boundary buys, that the text cannot close the fence, comes from escaping instead (`exar.text.tagged`, which also frames the document scope block): the `<` of every opening or closing tag the text carries itself is sent as `&lt;`, so it can neither close the fence nor speak as `<system>`, which leaves all other content as it is.
It is spotlighting, which lowers the success of injected instructions rather than preventing it, so what a run may do once it read untrusted content stays the actual boundary.
The fence is a pure function of the stored part, applied in `prepare_model_request`, the last step before the wire, so the stored conversation keeps the bare results and every request renders the history it replays byte for byte, the memory read a capability enqueues included, which keeps the provider's prompt cache valid (`tests/unit/test_prompt_cache.py`).
Only a call that succeeded is fenced: a denial, a failure, and a retry prompt are worded by the harness, whose notes join the retry prompt's text, the answers to `ask_user` are the user's own, and the notes the harness and an approval add to a result trail the text as items of their own.
`ToolOutputFence.after_tool_execute` runs innermost, ahead of the spill and every note, and gathers every text a tool returned into its leading string, such as the blocks of an MCP result, so every string after it is a note and the fence covers all the tool wrote.

### Spilling bulk output

A tool return longer than `settings.llm.tool_output_max_chars` is saved whole under the conversation's `/tmp/.tool-results/` and replaced by a preview by `agents.guards.ConversationSpill`, pydantic-ai-harness's `ToolOutputLimits` spilling into `agents.guards.SavedResults`, its `OverflowStore` over `/tmp`, carried by every agent over the user's deps.
The harness decides rather than the model, since a model-facing "save to file" argument was misused again and again.
The file is `<digest>.txt` with the text the model would have read, its digest that of the run, the call, and the retry.
`tmp.save_result` commits it through the gateway as the host into the reserved folder, and the quota evicts the oldest saved results when needed, never anything else.
The preview is the upstream marker naming the file and its size and how to read it back with `read_tool_result`, which `ToolOutputLimits` adds beside `grep_tool_result` and `DeclaredEffects` declares `read` like it (`guards.SPILL_TOOLS`), then the head and tail of the text, and any tool reading `/tmp` reads the file too.
A content list bounds only its leading text and keeps its non-text items, and a run without `/tmp` or a result that does not fit is only clamped, with a count of what was left out.
The `data-tool-output` chunk is what the client cites, so it stays even when its data went to the file too, and is dropped only when it serializes past `settings.llm.tool_data_max_bytes`, since the frontend loads and the database stores every chunk of a conversation.
It is encoded once, by `ToolOutputChunk.data_json`, which is what the spill measures and saves and what the chunk streams, while the database dumps the message it rides in on its own.
Every tool view falls back to the text, which names the file, and the data of a tool is what backs its text and no more, so grep's counting modes carry the files they list without lines.
Grep is `exar.tools.files.Grep` over the workspaces with the `Ripgrep` engine, which fails when the tool is built without `rg`, and it counts every file with one parallel `rg --count` per root, and content mode then reads the lines of only the files the first `max_results` matching lines reach, in path order, so every mode knows the total while holding only what it shows.
An original is left out where its markdown description matched too, decided on the counted files of its root, so a glob that skips the description keeps the original.
Tools keep only data budgets (`limit`, `max_results`, `max_chars`, widths), and the `max_chars` of `read_document` and `web_fetch` is `agents.common.WINDOW_MAX_CHARS`, nine tenths of the spill threshold, so a numbered window stays under it.
Every budget can be lifted by the model: counts stop only at ceilings that bound memory rather than context (10k grep lines and listed entries, 1k search results, the 100k table rows of `Table.max_rows`), `read_document` and `web_fetch` page by `offset` through one `exar.text.fit_window`, and `full_lines` lifts the per-line or per-cell clip of `read_document`, `grep`, `search`, `web_fetch`, and `query_table`, each clipping through an `exar.text.LineClip` (`exar.tools.common.line_clip`) that names its own cut.
`run_python` names a cut of its rendered return value, which `print()` gives whole, and the spill clamps a failed call's retry prompt at the same `tool_output_max_chars` without saving it.
Listings name what they left out through `omission_hints` (`include_ignored`, `max_depth`, `max_results`), each count being what changing that argument would reveal.

## The Python sandbox

`run_python` is a Monty sandbox with no network or host filesystem, and its time and memory budgets surface as `TimeoutError` or `MemoryError`.
A failed program is a `ModelRetry`, and `llm.retries` ends a turn after that many failures of one tool in a row.
A program is inline `code` or a `.py` `script_path` reloaded on every call, so the model can edit and rerun it.

### The mount

A program opens a document by the same canonical path as everything else (`~/reports/q1.md`), and the conversation's folder is mounted at `/tmp`, so no mount prefix is ever added or stripped.
Any other absolute path is refused with the roots named.
The working directory is `WORKSPACE_MOUNT`, so `/workspace/~/...` also resolves inside a program, but tool arguments refuse that spelling, since the approval gate shows the declared path.

`tools/workspace_os.py` is an `AbstractOS` routing every operation through `resolve_accessible_file` and `entry_visible`, so the `DocumentFilter` stays one predicate, a hidden document reads as absent, and legacy encodings decode.
Routing is decided once in its `dispatch` override, so no method can forget it.
Bytes are refused in either direction, so a document without a text form stays `read_binary_document`'s.

No host function duplicates the mount: `open` and `iterdir` are the read tools, `re` is grep, and `json` is jq.
Which modules exist is left to the model to discover through `ModuleNotFoundError`, since no list stays current with Monty.
The environment mirrors a shell's (`PWD`, `TMPDIR`, `LANG`, `HOME` as `/workspace/~`, `USER`), with no `PATH`.

### Writes from a program

A program changes files with ordinary `Path` and `open` calls, which `WorkspaceOS` records in a copy-on-write overlay that every later read consults, since a filesystem callback can neither await the gateway nor stop for an approval.
A rename records a `Ref` and copies no bytes, and the first touch of a disk file records its `ContentStat` as the basis, the whole tree for a removed or moved directory.
Limits are raised inside the program, so the model can correct it: the writable span (`agents.tools.write.program_writes`), hidden `.assets`, `writes_as_text`, the per-document cap, and `ChangesetLimits` from `settings.sandbox.max_changeset_*`.
A document keeps its extension when renamed, a directory is removed only when nothing is left in it on disk, and paths respell through `exar.memory.mounts.canonical`.

`/tmp` lives in the same overlay, but its nodes are not charged against `ChangesetLimits`, which exist to keep an approval readable.
A rename between `/tmp` and a workspace becomes a write plus a removal, and is refused for binaries and directories, while a rename within `/tmp` carries anything.
Its operations carry their bases as a workspace's do, so a staged one that drifted is a 409 too.

On success `tools.changeset.stage_changes` spells the overlay as one `Changeset[str]` in the gateway's terms, with no ordering needed.
Removing an entry's original removes the entry, while removing only its description, or splitting an entry's parts, is refused.
`agents.tools.write.program_writes` commits it at once where no item's root asks (`AppliedChanges`), and otherwise plans it and stores all of it, `/tmp` included, with `staging.stage` in the conversation's `changesets/` folder (`PendingChanges`), so it lands whole on approval or not at all, as a single write tool's call does.
A staged changeset is a file, since it is write-once state, applies only in its own conversation, and is routed again with the approver's stores.
Every saved turn keeps only the changesets a pending `apply_changes` call of its history still awaits or that it staged itself (`retain_awaited_changes`, by the time the turn started), so one the model staged and asked the user about survives to the next turn, while an older one denied, abandoned, or left by a rerun, a cancel, or a failed turn goes then, and an emptied `changesets/` with it, while one still awaited goes with its conversation, never by the idle sweep while the conversation exists.
A missing file reads as applied or discarded, while one that cannot be read or validated raises and stays.
`apply_changes(changeset_id)` plans it again in its validator for one `ApprovalRequired`, and a basis that moved since the program ran is a 409 the model answers by rerunning the program.

### Skill scripts

A stored script that defines `main` is called with `run_python(script_path=..., args={...})`: the host parses `main` with `ast`, feeds the script, then feeds a call it renders from `args` as literals in the same session, so Monty type-checks the names and types of the arguments against `main` before it runs, and `main`'s value is the result.
`args` without `script_path` or with keys that are no keyword names are refused, and a script without `args` runs as a plain program.
A script inside a skill gets `SKILL_DIR`, the skill's folder derived by the host from the path it resolved (`skills.skill_dir`), never from the model.
Such a script runs with exactly the authority of model-written code, so it needs no approval of its own.
`agents.tools.python.check_skill_script` checks a script by feeding its definitions, type-checked against every function a program could be handed, to a session with an empty filesystem and no host functions, and caches the outcome in memory by content, so nothing is stored and a skill is checked again only when it changed.
Scripts written for CPython (argparse, subprocess, third-party packages) do not pass and stay readable as references.

### Injected tools

Only tools whose answers lie outside the sandbox are injected as host functions by `tools/monty.py` (`search`, `query_table`), plus `complete`.
Only a `read` effect the policy allows outright is injected, since a running program cannot stop for an approval, and `web_search` and `web_fetch` are not injected, since a program looping over the links of a planted page would carry data out through its choice of URLs far faster than the model choosing them call by call.
A function is gated like the tool of the same name, by `UserDeps.policy`, which unions `settings.tools.disabled` with `deps.disabled_tools` itself, so a `UserDeps` built without that union still cannot hand a program a disabled tool, and it honors the people-only and derived flags too.

The functions are taken by name from the very list that builds the explore tools (`agents.tools.explore.INJECTED`), plus `complete`, from which `SANDBOX_FUNCTION_NAMES` and `INJECTABLE_TOOL_NAMES` follow, and a function's guidance (`COMPLETE_INSTRUCTIONS`) rides with it.
The stubs are rendered by pydantic-ai's `FunctionSignature` from each function's tool definition, its return schema being the type of its `ToolReturn` data, and a host function passes the program that data.
A new injectable is justified only where the mount cannot do the job.

`complete(*, prompt, reasoning=None)` is a tool-free model call for programs alone (`tools/complete.py`, injected and never registered), run on the turn's model (`llm_config.turn_llm`) without reasoning unless the program names an effort.
`completion_max_tokens` caps the answer, and a reasoning call gets that effort's thinking budget on top where the runtime enforces one (`llm.thinking_model_settings`), so thinking never eats the answer.
Its calls count toward the turn's usage and request limit, and `UserDeps.completions` (`agents.common.Completions`) enforces `completion_max_calls` before the first await, bounds `completion_concurrency`, and keeps a request slot free for the parent's response.
Those refusals reach the program as a catchable `ValueError`, while a spent turn budget ends the turn, and `completion_timeout_seconds` bounds each call, since Monty's clock excludes host time.

A program receives the whole structured `data` channel as plain dicts and lists matching the declared result type, since the text budgets exist for a context window a program does not have.

### Declarations and the type check

`monty_surface` builds the declarations the model reads (`sandbox_api_instructions`, composed per run) and the sandbox takes as `type_check_stubs`, rendered by pydantic-ai's `FunctionSignature` from the argument and serialization-mode return schemas.
A function the model also holds as a tool is declared with its signature and result records but a one-line pointer for its description and arguments, which it already read in the tool list, so `query_table` and `search` are not documented twice per request.
The stubs add one untyped `open` line, since `open` is host-provided and a hand-typed handle would reject programs the interpreter accepts.
`settings.sandbox.type_check` is on by default, and `SANDBOX_TYPE_CHECK_INSTRUCTIONS` is added only where it is on.

### Where a tool lives

`settings.tools.sandbox_only` injects a tool and drops it from the tool list, limited to `INJECTABLE_TOOL_NAMES` and overridden by `disabled`, which saves sending both a schema and a declaration.
The catalog lives in the agent instructions rather than `run_python`'s description, so the tool definitions stay byte-stable for caching.
A tool stays registered while the frontend consumes its tool part (citations from `search`) or a refusal hint points at it (`query_table` through `query_hint`).
`build_capabilities` unlists them from the main agent's tool list alone, and `tool_infos` leaves them out of `GET /tools`.

### Budgets

The read budget is per document (`max_document_chars`), not a running total, since the host holds one decoded document at a time, and it is checked by size before decoding (`check_read_budget`) and by length after.
Inline `code`, the stored script, and every written file answer to the same cap.
`max_memory` bounds what a program retains, `max_feed_duration_secs` its bytecode, and `max_total_sleep_secs` its sleeping, while `max_wall_seconds` bounds the time it waits on host calls: past it a running call is cut off and every later one refused, as a `TimeoutError` the program catches to finish with what it has, so no timer has to stop it and lose what it paid for.
`request_timeout_seconds` is the pool's backstop for one worker turn.
The lifespan owns the worker pool (`sandbox.py`), and every call takes a fresh session, so no program sees another's state.

`HostCalls` applies one policy to every host function: `settings.sandbox.max_host_calls` caps a program's calls before the first await, a `Retry` or bad argument becomes a `ValueError`, and any other failure a `RuntimeError` named by type alone, so no host details leak.
Monty's `max_suspensions` (`settings.sandbox.max_suspensions`) counts every round trip to the host, each file operation and name lookup too, so it sits well above `max_host_calls`, since its default of 1000 ends a program after about 500 file reads.
`UsageLimitExceeded` (`tools.monty._TURN_ENDING_ERRORS`) ends the turn however the program handled it.
Each call is kept as a bounded `HostCall` in `PythonResult.calls`, and a failed program's retry lists the first 20 before the traceback.

## Video and animated media

Vision chat models over the OpenAI Chat Completions protocol accept no video, so animated media is represented as at most `exar.ingest.MAX_FRAMES` evenly sampled, downscaled, timestamped still frames.
Videos are sampled with OpenCV (`exar.ingest.sample_video`, isolated by `media.ISOLATED`), whose wheel bundles an LGPL FFmpeg so no ffmpeg binary ships, and animated GIF, WebP, and APNG with Pillow (`exar.ingest.sample_animation`, isolated the same way, and only for those formats), then captioned by `exar.ingest.caption` with the aux model (`pipelines.vision.Vision`) into the `<stem>.md` description.
`read_binary_document` attaches the same frames (tagged `<path>#t=<ts>s`) instead of the container bytes, while still images are downscaled to `exar.ingest.MAX_DIMENSION` (`exar.ingest.downscale`).
Once pydantic-ai can send native video, the swap is local to the video and animation readers of `exar.tools.media`, `workspace.describe`, and `media.py`.
