# Hivegent Backend

The invariants this service is built on and the reasoning behind them.
The system shape is in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), the client half in [`../frontend/README.md`](../frontend/README.md).

`uv run pytest` is the whole test suite and needs no services, since every test is stateless and stubs the paths that would hit PostgreSQL with `monkeypatch`.
Migrations, the running server, and retrieval are covered by the dev stack and manual smoke tests.

## Interface language

The frontend sends `Accept-Language`, and `LanguageMiddleware` makes it the ambient language of the whole request, streamed body and background tasks included.
Every text a user reads is a `Localized` value from `l10n.py` with one field per language, so a missing translation is a type error, resolved with `.current` where it is raised.
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
Multi-row sequences no constraint can guard take a transaction-scoped advisory lock, such as `db.conversations._lock` in `append_branch`.
The primary keys, unique constraints, and `documents.single_owner` stay as the safety net.
The one accepted exception is a best-effort last-write-wins update with no constraint to violate, such as a throttled activity timestamp.

### Schema and migrations

Alembic owns the schema, and the migration tree lives in the package at `src/hivegent/migrations/`, so revisions ship with the wheel.
Chunk metadata, text, and vectors live in the `chunks` table and cascade with their document, so there is no separate vector index.
The FastAPI lifespan runs `apply_migrations()` before accepting traffic and refuses to boot when the live `chunks.vector` dimension differs from `settings.embedding.dimension`.

Revisions are written by hand, since `--autogenerate` needs a live PostgreSQL with `pgvector`.
Every change to `db/models.py` gets a matching revision under `migrations/versions/` in the same commit, named `YYYYMMDD_HHMM_<revision>_<slug>.py`, with `down_revision` set to the current head, symmetric `upgrade()` and `downgrade()`, and constraint names as the naming convention on `Base.metadata` produces them (`pk_users`, `fk_documents_owner_user_id_users`), wrapped in `op.f(...)`.
Verify against the dev database, started by the dev shell or `nix run .#hivegent`:

```sh
uv run alembic upgrade head
uv run alembic check
```

`hivegent migrate [<revision>]` and the standard `alembic` subcommands work for ad-hoc use.
In production the systemd unit from [`nixosModules.default`](../nix/nixos/service.nix) migrates during startup, and a failed migration exits non-zero, which trips `Restart = "on-failure"`.

## Configuration

### Secrets are credential files

Settings layer init arguments over environment variables, `.env`, credential files, and the TOML file.
A credential is a file in `$CREDENTIALS_DIRECTORY`, which systemd sets for the unit and the container image points at `/run/secrets`.
It is named `hivegent.<NAME>` for the variable `HIVEGENT_<NAME>` it replaces, e.g. `hivegent.LLM__API_KEY`, so the bridge's `hivegent-bridge.*` credentials stay apart in a shared directory.
On NixOS the unit also imports every `hivegent.*` credential from the system credential store, see `services.hivegent.credentials`.
Unlike an environment file, a credential is readable only by the unit's user and stays out of every subprocess's environment.
Every secret setting is a `SecretStr`, unwrapped by `config.reveal` only where a client needs the value.

## Workspace content

### The filesystem is the source of truth

The tree under `data/workspace/<store_key>/` is authoritative for document content, and the `documents` and `chunks` rows are a derived index.
`workspace.sync_entries_from_disk` is the one idempotent ingest path, which re-chunks only when the `content_digest` changed, behind an `entries.ContentStat` pre-filter that can cost a read but never misses a change.
The digest and stat are cleared together by `upsert_document` and stamped together by `set_content_state` once the chunks are durable, so a null digest means "not indexed yet".
A description with no prior row is stamped `origin = imported`.

`reconcile.reconcile_store` runs at boot over every entry on disk and every row, drops rows whose description vanished, and first renames non-NFC files to NFC, so every `stem_path` is NFC.
It never deletes workspace files, since a file without an entry is inert content rather than an orphan.

### What becomes a document

`entries.is_description_file` and `entries.is_projectable_original` are the content-versus-document policy.
Markdown files are descriptions, and a non-markdown file whose projection is a verbatim copy of its own text (config, data, source) gets its `<stem>.md` derived by the ingest pass.
Binaries and converter- or vision-backed formats (`.csv`, `.html`) stay inert on disk until uploaded or reconverted, since conversion has no place in a boot sweep, but they are listed and deletable.
`converters.projection_for` is the one routing table for what projection a filename gets, read by uploads (`workspace.prepare._prepare_upload`), the ingest pass, and rediscovered rows (`workspace.metadata._REDISCOVERED_KINDS`), so they cannot drift.
Deleting only a text original's `<stem>.md` by hand does not retire the entry, since the next ingest derives it again.

### Paths

A document path is prefixed by its workspace (`~` personal, `@<group>` group), and a relative path is shorthand for one under `~` (`Scope.implies`), while filter arguments without a prefix still span every workspace.
A path leading with `/`, `~`, or `@` that names no known root is refused with the roots named (`tools.base.workspace_root_hint`), so an unknown group is never written under `~`.
The grammar is stated once per surface (`WORKSPACE_PATH_INSTRUCTIONS`, also the FastMCP `instructions`), and messages always render canonical paths (`workspace.paths._shown`, `store.scope.render`).

Access derives from the request's `User` alone (`User.store`, `User.group_stores`), and `UserDeps` holds that `User` rather than stores, so no route, run, or subagent can reach a workspace the token does not grant.
HTTP and MCP build the `User` through the same `auth.user_from_claims`.
Writes span the personal workspace plus the writable groups, reads every readable one.
A group `Casebase` defaults to a read grant (`Casebase.writable`), and the casebase lock (`workspace.locks._lock_of`) and every document row write (`db.documents._writable_filter`) refuse a read grant with `AccessDenied` (403).
Only the admin routes and reconciliation state a grant of their own.
Owned rows are named through one predicate per table (`_owner_filter`, `_owned`), and the conversation and memory repositories take a `User` or an `OwnedConversation` rather than an id string.
Disk reads resolve through `Casebase.locate`, so neither `..` nor a symlink escapes a workspace, `query_table` refuses Polars' `read_*` table functions, and `jq` refuses module directives and runs without the server's environment.

A group is identified only by its OIDC groups-claim ID, in the `groups` row, `documents.owner_group_id`, the `group:<id>` directory, and the `@<id>` prefix.
`auth.parse_group_claims` is the one place that knows how a provider spells the claim, and display names are client labels only.

Path strings are folded to NFC wherever they enter (`config.normalize_unicode`), since macOS hands out decomposed filenames, while file content is never normalized.

### Text, encodings, and what may be written

Every read of user bytes goes through `text.decode_bytes` or `text.read_text_file` (BOM, strict UTF-8, CP1252 fallback, `None` for binary), so a hash taken by a read tool matches when an edit tool re-reads the file.
Writes are always UTF-8, and a transcode is reported rather than applied silently.

A document is writable exactly when it is readable as text, since both sides ask `converters.vision_media_type` first and the decoder second, and every refusal ends in `tools.base.sidecar_hint`, which names the `<stem>.md`.
A markdown description is written and re-indexed in place under the casebase lock.
Any other file is an original, so the gateway converts the new bytes as an upload would (`changeset._convert`) before taking the lock, checks the hash again under it, and leaves the previous entry intact on failure.

Creating a file decides by name, since it has no bytes yet: `converters.writes_as_text` refuses a suffix on `converters.BINARY_SUFFIXES` and admits every other, so `.csv`, `.html`, and `.svg` are created as text.
That is deliberately not `is_projectable_original`, which only answers what the ingest pass may derive by copying.
A new original must claim a free stem, and a binary is replaced by uploading.
The refusal (`converters.BINARY_WRITE_REASON`) comes from `tools.mutations.resolve_text_target` before the approval prompt, and from the sandbox mount when a program writes a new file.

Every served and shown media type comes from `converters.formats.media_type`, derived from the path rather than stored.
It asks Python's built-in table with the declared image, video, and LLM tables and the formats documents embed laid over it, never the host registry, since that one differs between machines and mislabels source files.
The document routes serve a file as plain text when its type is unknown or textual and it decodes, and anything else under its own type as a `nosniff` attachment, so an SVG reaches an `<img>` as an image but never renders in a tab.
The client previews any original whose type is an image or a video, an SVG included although it is indexed as text.

A whole write to a delimited file is checked against its header width by `tools.mutations.check_delimited_rows`, with the separator from `converters.DELIMITERS`, the table `query_table` reads with too.
Edits are not checked, since they change a file they did not build.

### A conversation's `/tmp`

`/tmp` holds a conversation's working state that is never a document: intermediates, state for a later `run_python` call, scripts, and spilled tool results.
Each conversation owns `<data_dir>/conversations/<id>/` (`OwnedConversation.folder`), outside every workspace, holding its `tmp/` and the `changesets/` its programs staged.
Ownership comes from the `conversations` row, never the path, and an `OwnedConversation` is obtained only from the database or minted for a new chat with an id that must be a safe path segment, so no caller can name another user's folder.
`tmp.tmp_dir` is the one place an id becomes a path, and every `{conversation_id}` route resolves through one dependency.

`/tmp` is a root beside the workspace roots: `UserDeps.search_paths` appends `tmp.tmp_search_path`, so the read tools reach it with no code of their own, and it is never reconciled, indexed, searched, filtered, or announced.
A subagent shares its parent's `/tmp`, while a caller without a conversation (MCP, the admin tool console) has no `/tmp` root.

Every `SearchPath` carries a `CommitPolicy`, `Gated` for a workspace (approval as the mode asks) or `Direct` for `/tmp` (written straight, no approval), and `CommitPolicy.reserves` names what no ordinary change may touch, the `.assets` payloads and `/tmp`'s reserved `.tool-results/`.
`/tmp` commits through the same changeset gateway as a `workspace.operations.Folder` root (`tmp.tmp_root`), so occupancy, chains, basis checks, and rollback exist once.
`/tmp` is writable in every mode, so the `write` feature is offered in every mode, while `WRITE_INSTRUCTIONS` and `apply_changes` are limited to modes that may write the workspaces and to runs with a conversation.
A move between `/tmp` and a workspace is refused by the gateway, since no one commit spans both.

`settings.tmp.max_bytes` caps a folder through its `Quota`, charged with what a changeset adds, so a refused change writes nothing and an over-cap folder may still shrink.
`GET /api/documents/{filepath}` reads a `/tmp` path when given a `conversation_id` the user owns.

`conversation_files` ends a folder's life, since no row cascade reaches it: deleting conversations removes rows and folders together, a reset removes every folder, and `DELETE /api/tmp` empties only each `tmp/`, keeping pending approvals.
`sweep_conversations` removes a whole folder once nothing below it changed for `settings.conversations.ttl_hours`, runs in a background task after the migrations and every `sweep_interval_hours`, needs no database, and checks each folder again under its `/tmp` lock.

`TMP_INSTRUCTIONS` is shared by the `python` and `write` features, and `PYTHON_INSTRUCTIONS` names `/tmp` as the home of a rerunnable `.py`, since a `.py` in a workspace is an original that gets chunked.
The write receipt for a `/tmp` `.py` points at `run_python`'s `script_path` through a `MutationHint`.

- TODO(binary): accept binary files of any suffix in `/tmp`, with `run_python` reading and writing bytes under a per-file cap.
- TODO(binary): let `run_python` write or move a binary into a workspace as a staged item that runs the upload conversion at apply time and shows as an upload in the approval.

### Notifications

Every workspace change reaches clients as one `changes.WorkspaceChanged` on the per-owner SSE feed, carrying the changed scopes plus moves and deletes, so a client can follow the paths it keeps.
`apply_changeset` announces one per commit, and every other inline mutation must announce through `workspace_events.announce_workspace_changed`, or other clients stay stale.
The feed skips the `X-Client-Id` that caused a change, so the change routes return the same `WorkspaceChanged` to the asking tab, while `workspace.Gateway` (agent, MCP, host) skips no client.
Delivery is per owner, so group members other than the writer are not refreshed yet.
Events are transient, so the client re-reads every scope it holds on each handshake.

### Preparing for a read-write shell tool

A future shell tool will run native commands in an isolated session, and an explicit checkpoint folds an approved session diff back into the filesystem and index.

- TODO(shell): run commands in a dedicated sandbox runner with a narrow session API, never through a shell or container socket in the backend.
- TODO(shell): run each session as an unprivileged user seeing only the store's working copy at `/workspace` and the conversation's `tmp.tmp_dir` at `/tmp`, with no secrets, sockets, capabilities, or devices, a read-only root, and explicit resource limits (`tmp.max_bytes` for `/tmp`). `subprocesses.run` is unsandboxed and only safe for fixed-argument tools.
- TODO(shell): run against an isolated copy or overlay of `/workspace`, so the casebase lock is never held while commands run, with a copy-based fallback for macOS. `/tmp` is written directly, as the Python sandbox does.
- TODO(shell): compute the session diff in trusted runner code, surface it for approval, and make checkpoints explicit.
- TODO(shell): fold the diff back through `workspace.apply_changeset`, reusing the entry rules of `tools.changeset.stage_changes`.
- TODO(shell): keep an entry whose sync failed out of retrieval, retain its dirty state for retry, and report the checkpoint unsettled until every entry succeeds.
- TODO(shell): decide which shell-created binaries and converter-backed formats become entries at fold-back.
- TODO(shell): keep all workspace access behind `Casebase.workspace_dir` and all `/tmp` access behind `tmp.tmp_dir`, so each mount root is injected in one place.

## Conversations

### A server-authoritative message tree

The database is the source of truth for chat history.
Messages form a tree through a nullable `parent_id`, and the active path is the newest message walked up to the root (`_load_active_path`), so no branch pointer is stored and there is no FK cycle.
No message count is stored, since the active-path count is a path aggregate.

The client sends only the new message plus `trigger` and `messageId`.
`_run_chat` replays the active-path prefix up to the fork point (`resolve_fork`), and `append_branch` persists only the new tail below it: a submit forks at the leaf, an edit at the edited message's parent, a regenerate at the nearest user turn, so prior branches stay as siblings.
`dump_messages_with_ids` anchors each `UIMessage.id` to its node id and annotates forks for branch navigation.

A freshly sent message carries only an SDK-minted id, so `_run_chat` reserves its node before the run and returns it as `X-Message-Id`, like `X-Conversation-Id` for a new conversation.
Only a request carrying a user prompt reserves an id, and `resolve_fork` treats `messageId` as an edit only when it names a user turn on the active path, since the SDK also sends it when continuing after an approval.
For the same reason `ChatAdapter` keeps only the client's user messages, since appending the approval resend would duplicate a `tool_call_id` and make the model reissue the call.

An approval or question the user never answers is closed by the next turn (`abandon_pending_calls`), stored as its own node before the run, since pydantic-ai would otherwise repair the dangling call as "interrupted" and the model would reissue it on every turn.

Persistence is hard-fail: a failed write surfaces as a trailing error chunk, and is only logged on a client disconnect.
`run_and_persist` persists exactly what `capture_run_messages()` holds.
A turn that never finished first gets `close_orphan_tool_calls` and `record_turn_error`, while a clean finish is stored untouched, so a pending approval reloads as one.

### Client-posted messages are validated strictly

The Vercel AI request models forbid extra fields, so a part the SDK sends that pydantic-ai has not transcribed fails the chat and import routes with a 422.
Such drift is fixed upstream, never by relaxing `extra='forbid'`, which is what stops a malformed `approval` from matching the unanswered variant and releasing a gated call.
`tests/unit/test_client_messages.py` pins the shape a real client posts.

### Tool approvals round-trip symmetrically

A decision's `reason` is the user's optional note, and the model-facing text is worded in the backend, in English.
A denial becomes `ToolDenied(denial_message(note))`, and an approval's note reaches the call as `RunContext.tool_call_metadata`, which `agents.approval.ApprovalNotes` appends as one more item of the return's content list, after `ToolOutputSpill` bounded it (`wraps=[ToolOutputSpill]`).
It is not `ToolReturn.content`, which would be stored as a user turn nobody typed.
`vercel.record_approvals` stamps each decision on the request carrying the returns, and `dump_messages_with_ids` restores it, so `approval.reason` always means the user's note.
That metadata is UI-owned, never sent to the provider, and rides on the request node, since `ToolReturnPart.metadata` is the tool-output chunk channel.
`ChatEventStream.handle_run_result` stores what each `ApprovalRequired` carried under `approvalMetadata`, so the prompt shows the same server-resolved paths live and on reload.
An approval overtaken by a new message is a denial on both sides, recorded by `abandon_pending_calls` and derived by the frontend over the live transcript.

### Questions are deferred calls answered by the client

`ask_user` raises `CallDeferred`, so the run ends with the questions pending and the client sends the answers as the call's output, validated against `AskAnswer` by `ChatAdapter.question_answers`.
A dismissal or an abandoned question is a failed return worded by the server (`DISMISSED_QUESTIONS`), and `dump_messages_with_ids` reopens a pending question as `input-available`.

### Compaction is a turn of the conversation

A compaction is a node of the conversation it compacts, a `ModelResponse` holding one provider-less `CompactionPart` whose `content` is the summary.
The tree keeps every message, so branches, exports, and the transcript stay whole, while pydantic-ai treats the part as the boundary of the instruction window and of the tools it derives from history, and the Vercel adapter round-trips it as a `data-compaction` part.

`summarize_conversation` appends `COMPACT_PROMPT` to the conversation's own message list under the same `RunPrefix` as its chat turns, so the provider's KV prefix cache covers everything but the prompt.
That is why a compaction runs inside the chat route, `build_run_prefix` composes the prefix for both, and the history is loaded from the database rather than the browser.

Room is made by dropping messages from the tail, which keeps a shorter prefix of the cached block.
Sizes come from `ModelResponse.usage`, so no tokenizer or context window setting is needed, and `_cuts` offers only prefixes where every tool call is answered and that do not end on a fresh user prompt.
The first attempt sends the longest prefix the endpoint has served, and only a refusal sheds the reserve (the summary's completion cap plus `_COMPACT_PROMPT_RESERVE_TOKENS`), for at most `_MAX_ATTEMPTS`.
The subagent recovery path (`_safe_summarize`) reuses this summarizer, since trimming to the last answered call also makes a crashed run's history legal.
`SUMMARY_MAX_TOKENS` is clamped to the request's own `max_tokens`, and `COMPACT_PROMPT` bounds the summary by structure, since a length cap makes the model drop sections.

A compaction fires only after the provider refused a request (`is_context_overflow`), never on a forecast, since the window, reserve, and threshold are not reliably known.
`CompactOnOverflow` (`agents/compaction.py`) then compacts the run's history at `compaction_cut` and retries, and the `Compacted` event shows it live.
The cut goes after the refused request where that closes the history, so the summary covers everything the client has seen and the retry continues from it with an empty request.
A refused prompt is never summarized, so the cut goes ahead of it and the retry sends it again.
A regenerated prompt is persisted already, so its compaction is stored ahead of it with `insert_above`, which puts the compaction between the prompt's parent and all of that parent's children, since every branch from there shares the summarized history.
A cut is one of those same prefixes, so the summary and the window agree on where history can be cut.
A window that overflows on its own is not compacted again, and a cut past the latest prompt waits for a model to answer it, so a prompt is never summarized with only the memory read reported beside it.
A response cut short mid tool call has already streamed, so it fails the turn instead.
A chat request flagged `compact` carries no message and compacts the active path at the same cut, which is the header's button.
A path ending on a failed prompt is compacted ahead of it, and one ending on an open approval or question is not compacted, so the request leaves it pending.

`CompactionWindow` (`agents/window.py`) sends every request the window from the latest compaction onward, with the summary as user text in `<conversation_summary>` tags.
It renders from the stored node alone, so every later turn sends the window byte for byte and the provider caches it like any other prefix.
It stands in for upstream #10573, which sends provider-less `CompactionPart`s to Chat Completions models, and goes once a release includes it (#7289).

### Run state appends its changes

The instructions and tool schemas precede the whole history, so anything rendered into them that changes during a conversation has the provider reread all of it.
The mode and the document scope (`agents/context.py`) are therefore `on_change='append'` instructions: the first value stays in the prefix, and pydantic-ai appends each later change to the history as an `InstructionDeltaPart`, persisted with the turn, so a change only extends the cached prefix.
The UI adapter leaves these changes out of the messages the frontend receives.
Instructions carry operator authority, so only state the application controls goes there, a fixed mode note and the paths the user selected.
The modes that may write compose the same instructions and tools and differ only in their mode note, while read mode drops the mutating features and starts a new prefix.
The chat templates of vLLM and llama.cpp reject a `system` message after the first user turn, so the self-hosted profile turns `supports_inline_system_prompts` off and a change goes out as `<system>`-tagged user text.
Both change only between turns, so that text joins the user's prompt: a user message in the middle of a turn is what Qwen's template takes for a new query, rendering the steps before it without their reasoning, which voids the cache.
`tests/unit/test_prompt_cache.py` drives the production vLLM model through stored turns and asserts each request extends the previous one byte for byte and adds no user message mid-turn.

### Memory is a tool result

The memory is model-written, possibly after reading a web page or a document, so it never becomes an instruction.
`MemoryNote` (`agents/tools/memory.py`) reports it as a synthetic `read_memory` call and result, enqueued into the first request of a conversation, and of every window a compaction opens, and persisted with it, and the model can call `read_memory` again after a change.
`write_memory` appends to the memory or replaces one exact passage, so a save never has to repeat what it keeps, and it edits under a row lock, so two conversations saving at once do not overwrite each other.
`limits.max_memory_chars` bounds the memory, which keeps the note at the start of every conversation small, and an edit that shrinks an oversized memory is still accepted.

### One budget per turn

`turn_usage_limits` bounds a chat turn, and a subagent runs on the parent's `ctx.usage`, `ctx.usage_limits`, and `ctx.conversation_id`, so the whole turn spends one count and traces as one.
`LimitNotes` (`agents/guards.py`) reads the same limits and each tool's retries, and names the limit that will stop the run once three quarters of either is spent.
The note arrives mid-turn, so it rides what the model reads next rather than a turn of its own: a failing call's retry prompt, or the first result after the request budget runs low, once per run.
It rides on every agent, so a subagent on the shared budget is warned too.
Every agent toolset is built by `agent_toolset`, which carries `llm.tool_timeout_seconds`, since pydantic-ai applies an agent's `tool_timeout` to its own tools alone, and a tool's own timeout overrides it, which is how `explore` and `run_python` declare `SELF_BOUNDED`: `explore` bounds the subagent and the summary recovering it, and `run_python` its host calls.
`invoke_agent_tool` runs a tool through its feature's toolset, so the MCP endpoint and the debug route are bounded the same way.
Each purpose runs its own named agent (`chat`, `explore`, `summarize`, `title`, `complete`, `debug`, `report`), since the name is what Logfire attributes spend to.

### The chat debugger reads any conversation

`/api/debug/conversations/{id}/chat` lets an admin ask the `debug` agent about any user's conversation, found by `any_conversation` without the ownership check, and every access is logged.
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

Citation line chips only show evidence captured in persisted read, grep, or search outputs, kept apart by tool call id, and a chip without supporting output is disabled.
Opening the document never applies a historical line anchor to current content.

A chat turn attaches only images in `converters.INGESTIBLE_IMAGE_MEDIA_TYPES`, so nothing converts on the chat's latency path, and anything else belongs in a workspace, which indexes it once instead of re-sending it every turn.
`/settings` serves the same table and size cap as `AttachmentLimits`.

## Document tools

The tools under `tools/` must not read `hivegent.config.settings`: every tunable is a dataclass field with a default set where `agents/tools/*` instantiates the tool.
They speak a runtime-agnostic interface (`tools.base`) with the adapters `tools.pydantic_ai` and `tools.fastmcp`, so the package can ship on its own, while the app's MCP endpoint serves the agent's toolsets instead.

Read tools serve the markdown projection, which retrieval and citation anchors build on.
Tables (`converters.TABULAR_SUFFIXES`), JSON (`converters.JSON_SUFFIXES`), and vision media (`VISION_MEDIA_TYPES`) each have one table that gates the specialised tool and makes `read_document` point at it.

### Read tools are list-first

A read tool takes a list of items, and a one-item list asks for one, so no tool has a singular twin.
`tools.base.run_batch` deduplicates items, serves them in request order with bounded concurrency, renders each under a `==> key <==` header within its `BatchShare` of the budgets, and turns one item's `ToolRetry` into its `ItemFailure`, refusing only when every item failed.
Batching lets a call share work, so `search` fuses its rankings by reciprocal rank and `query_table` loads each file once for every query.
The adapters accept a bare item where a list is declared (`accept_scalar`).
`grep` and the `path` of `list_documents` stay single.

### Scope and filters

`included_documents` is advisory and only named in the prompt, so a run can follow a reference out of the selection.
Only `excluded_documents` becomes a `DocumentFilter`, enforced by the path tools through `SearchPath.filter_func` and by retrieval in `resolve_accessible_document_ids`.
The planner refuses a directory move that would carry an excluded entry (`PathFilter.hides_within`), with the run's filters for an agent change and the approver's current ones for a staged changeset, while the HTTP routes pass none.

A filter argument naming a directory no accessible root holds is refused (`tools.base.resolve_directory`, `missing_directory_retry`) rather than answered empty, which would read as an empty workspace.
The refusal names the caller's own spelling and the roots, without demanding a prefix.

### Mutations

Every write, edit, move, delete, new directory, and upload is an item of one changeset committed by the one gateway in `workspace.changeset`, the HTTP routes included.
Operations are generic over the location type, `str` where tools and staged programs spell paths, and `workspace.Location` once `workspace.operations.route` sent them to their root.
Items apply at once, like a diff of two states: sources and bases name the workspace as it is, destinations and targets as it ends up, so chains, swaps, rotations, overwrites, and case-only renames fit in one changeset.

`plan_changeset` resolves sources into units (an entry with its description, original, and assets, or a directory tree) and checks the final state rather than an order: each unit is claimed once, each final path filled once, every destination is free or vacated, and a new original's stem holds no other entry.
So a move onto an occupied path needs a delete of it in the same changeset, a destination naming a directory that stays means into it like `mv`, and a write follows the move that carries its file.
It returns the `changes.ChangesetSummary` an approval shows, built from the resolved effects.

`apply_changeset` converts uploads and converter-backed originals without a lock, takes every involved root's lock in `store_key` order, resolves again under them, and installs files through one rename journal (`paths._Journal`) and rows in one transaction, so a failure restores both.
Moved units are parked under `.changeset-park/` inside the transaction, which lets a swap pass the `(owner, stem_path)` unique constraint, and rows are updated rather than recreated, so a moved document keeps its id and embeddings.
Indexing runs after the locks are released, and the commit is announced once.

An upload is a `workspace.Upload` item only the host builds, so a stored `Changeset[str]` never carries bytes.
An `overwrite` replaces the entry at its stem under the gateway's locks and keeps its origin, in place when the upload consists of the entry's files and by deleting the entry first otherwise.
`replace_original` and `reconvert` are uploads over the entry, and a change made while converting refuses the commit with a 409.
An index failure leaves the files with a null digest for reconciliation.

An asset description `<stem>.assets/<name>.md` is a unit of its own that only the host writes or deletes, refused unless its asset stays, and `workspace.generate_asset_description` runs the vision model before the commit takes the lock.

A `workspace.operations.Root` answers for its own rules (`path`, `scope`, `policy`, `quota`, `store`), so the planner and executor never ask which kind they hold.
A `Folder` such as `/tmp` has no store, so it skips rows, projections, in-flight guards, indexing, and announcements, and is left out of the summary and the `WorkspaceChanged`.
Only the host (`Gateway.host`) may write what a root reserves or let its quota evict.
Root locks come from one registry by `store_key` (`locks._lock_of`), and a commit stages beside every root it changes (`_staging_root`), so every step stays a rename.

`POST /api/changes` applies a list of operations (`server.models.ChangesRequest`) as one changeset within the request, and the single-item routes (`POST /documents/move/{path}`, `DELETE /documents/{path}`, the `/directories` routes) build one such operation through the same `_apply`.
A delete must name the kind it `expect`s, so a client never removes a folder by accident.
Each route answers with the applied `WorkspaceChanged`, since a move into an existing folder lands below it.
Uploads, writes, asset descriptions, rechunk, reconvert, and wiping a scope keep their own routes.

Paths compare as the workspace's filesystem compares them: on a case-insensitive one (`entries.folds_case`) claims compare by `entries.path_key` and existing paths are respelled as on disk (`entries.respell`), so only a destination's last segment keeps its spelling.
The tools respell in `tools.base.canonical_local_path` before any filter is asked, so `SECRET.md` cannot reach a hidden `secret.md`.
A basis is a content hash or an `entries.ContentStat`, which includes the inode, since a same-size replacement with a preserved mtime is the one change `(mtime, size)` misses.

The agent's mutation tools are list-first (`moves`, `paths`, `edits`) except `write_document`, and no argument is a glob, so the user approves exactly the paths that change.
`workspace.Gateway` binds the roots a surface may write, its filters, and the user it tells.
Each validator in `agents/tools/write.py` goes through `_gate`, which plans the changeset when an item routes to a workspace and raises one `ApprovalRequired` carrying the `ChangesetSummary` with canonical paths.
The commit is one changeset over the run's roots, so a batch spanning a workspace and `/tmp` lands whole or not at all.

### The image cap belongs to the gateway, not to the reader

`multimodal.max_images` is the serving gateway's per-request image limit (vLLM's `--limit-mm-per-prompt`), which rejects the whole request rather than the call that overfilled it.
`ReadBinaryDocumentTool` binds `max_pages` (refused, naming `pages=`) and `max_frames` (clamped) to it, and a batch divides the cap among the files that become images.
`agents.guards.PromptImageLimit`, carried by every agent in `agents/app.py`, replaces all but the newest `max_images` images in the outgoing request with a note, since parallel reads, attachments, and replayed history all fill one request.
It edits only the wire copy in `wrap_model_request`, never the run's history, which `before_model_request` would rewrite, and rebuilds what it touches, since parts are shared with that history.
`ToolOutputSpill` is the deliberate exception that replaces the recorded return.
Vision calls of the upload pipeline run on the tool-free `llm.complete` agent, so `workspace.describe` clamps its sampled frames to the cap itself.
`_accept_attachments` refuses an over-cap attachment set at send time, served to the composer as `AttachmentLimits.max_count`.

### Tables are queried, not read

`query_table` runs Polars SQL against the original CSV, TSV, Parquet, or Excel file, since a row-per-line projection wastes context and clips trailing columns.
Files are addressed positionally as `t`, `t2`, `t3`, so no path is interpolated into SQL, every file is registered so `SHOW TABLES` works, and omitting the query returns the first table's schema.
The dialect is named Polars SQL and anchored to PostgreSQL, which it follows upstream, so one word sets the model's priors on quoting, casts, and function names.
Failures say another function name may exist, since there is no function registry to vendor, and drop the resolved plan Polars appends, which a model cannot act on.

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

`jq` runs a filter against the original `.json` file, and omitting the filter returns the top-level keys and their types.
`.jsonl` is left out, since jq is handed one document.
The text is passed as read, so a malformed file is reported in jq's own words as a `ToolRetry`, and every value renders as one compact JSON line.

### Registered or excluded, never deferred

A tool is either in the model's initial context or not registered for the run, and there is no `defer_loading`.
A model whose tool list lacks a name works around a pointer to it rather than searching for it, and `OpenAIChatModel` has no wire-level deferral.
`settings.tools.disabled` is the operator's list of tool names, unioned with a request's `disabled_tools` by `build_capabilities` into one `PrepareTools` pass, which also covers MCP server tools and retracts a feature's instruction block.
It defaults to the two conversation tools, since `explore` covers past conversations, and `check_tool_settings` refuses an entry that matches nothing at startup.
User-configured MCP servers are added directly (`build_mcp_server`), and an operator who disagrees names their tools in that list.
`query_hint` keys its pointer to `query_table` on the entry rather than the requested path, since an uploaded table is served as its `<stem>.md`.

### The MCP surface serves the agent's tools

MCP serves the `explore`, `write`, and `subagent` features through one adapter (`mcp/adapter.py`), so a client calls exactly what a model calls, schema, validator, and gate included.
The other features need a conversation or a sandbox, and `tools.disabled` and `tools.sandbox_only` withhold a tool here as from a run.
Each call builds the caller's `UserDeps` in `interactive` mode with no conversation, and the served set is settled once at import by each tool's `prepare` hook, which withholds `apply_changes` and, without a main model, `explore`.
The gate's `ApprovalRequired` is answered with an MCP elicitation (`mcp/confirmation.py`) showing the `ChangesetSummary`, and its request state is a digest of the tool, arguments, and summary, so an answer cannot be replayed for other arguments or a changed workspace.
A return is its text bounded by `tools.formatting.truncate_middle` at `settings.mcp.output_max_chars` (`None` sends it whole) and its binary attachments as image or resource blocks, with no `structured_content` or `outputSchema`, since major clients hand structured content to their model in place of the LLM-tuned text or beside it.

### Spilling bulk output

A tool return longer than `settings.llm.tool_output_max_chars` is saved whole under the conversation's `/tmp/.tool-results/` and replaced by a preview by `agents.guards.ToolOutputSpill`, carried by every agent over the user's deps.
The harness decides rather than the model, since a model-facing "save to file" argument was misused again and again.
The file is `<tool>-<digest>.json` with the structured data when the return has any, else `.txt` with the text, and `ToolOutput.display_only` data such as a subagent transcript saves the text instead.
`tmp.save_result` commits it through the gateway as the host into the reserved folder, and the quota evicts the oldest saved results when needed, never anything else.
The preview is the head and tail cut at line ends around a note naming the file, its size, its entry count, whether the tool already truncated, and how to read on.
A content list keeps its non-text items, and a run without `/tmp` or a result that does not fit gets the same preview with a count of what was left out.
The `data-tool-output` chunk is what the client cites, so it stays even when its data went to the file too, and is dropped only when it serializes past `settings.llm.tool_data_max_bytes`, since the frontend loads and the database stores every chunk of a conversation.
It is encoded once, by `ToolOutputChunk.data_json`, which is what the spill measures and saves and what the chunk streams, while the database dumps the message it rides in on its own.
Every tool view falls back to the text, which names the file, and the data of a tool is what backs its text and no more, so grep's counting modes carry the files they list without lines.
Grep counts every file with one parallel `rg --count` per root, and content mode then reads the lines of only the files the first `max_results` matching lines reach, in path order, so every mode knows the total while holding only what it shows.
An original is left out where its markdown description matched too, decided on the counted files of its root, so a glob that skips the description keeps the original.
Tools keep only data budgets (`row_limit`, `max_results`, `max_chars`, widths), and the `max_chars` of `read_document` and `web_fetch` is `agents.common.WINDOW_MAX_CHARS`, nine tenths of the spill threshold, so a numbered window stays under it.
Every budget can be lifted by the model: counts stop only at ceilings that bound memory rather than context (10k grep lines and listed entries, 1k search results, 100k table rows), `read_document` and `web_fetch` page by `offset` through one `tools.formatting.fit_window`, and `full_lines` lifts the per-line or per-cell clip of `read_document`, `grep`, `search`, `web_fetch`, and `query_table`, each clipping through a `tools.formatting.LineClip` that names its own cut.
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
Limits are raised inside the program, so the model can correct it: the writable span (`agents.tools.write.program_paths`), hidden `.assets`, `writes_as_text`, the per-document cap, and `ChangesetLimits` from `settings.sandbox.max_changeset_*`.
A document keeps its extension when renamed, a directory is removed only when nothing is left in it on disk, and paths respell through `canonical_local_path`.

`/tmp` lives in the same overlay, but its nodes are not charged against `ChangesetLimits`, which exist to keep an approval readable.
A rename between `/tmp` and a workspace becomes a write plus a removal, each landing as its root commits, and is refused for binaries and directories, while a rename within `/tmp` carries anything.

On success `tools.changeset.stage_changes` spells the overlay as one `Changeset[str]` in the gateway's terms, with no ordering needed.
Removing an entry's original removes the entry, while removing only its description, or splitting an entry's parts, is refused.
`changeset_committer` commits it at once in write mode (`AppliedChanges`), and otherwise plans the whole changeset, applies the `/tmp` items, and stores the workspace items with `staging.stage` in the conversation's `changesets/` folder (`PendingChanges`).
A staged changeset is a file, since it is write-once state, applies only in its own conversation, and is routed again with the approver's stores.
It is discarded once denied or abandoned (`discard_unapproved_changes`), with its conversation, or by the idle sweep.
`apply_changes(changeset_id)` plans it again in its validator for one `ApprovalRequired`, and a basis that moved since the program ran is a 409 the model answers by rerunning the program.

### Injected tools

Only tools whose answers lie outside the sandbox are injected as host functions by `tools/monty.py` (`search`, `web_search`, `web_fetch`, `query_table`), plus `complete`.
Nothing that mutates is injected, since a running program cannot stop for an approval.
A function is gated like the tool of the same name, and `sandbox_surface` unions `settings.tools.disabled` with `deps.disabled_tools` itself, so a `UserDeps` built without that union still cannot hand a program a disabled tool.

The set derives from the tuples that register the tools (`EXPLORE_FACTORIES`, `WEB_FACTORIES`) and the class flags `Tool.injectable`, `Tool.registered`, and `Tool.sandbox_instructions`, from which `SANDBOX_FUNCTION_NAMES` and `INJECTABLE_TOOL_NAMES` follow.
A new injectable is justified only where the mount cannot do the job.

`complete(*, prompt)` is a tool-free model call for programs alone (`tools/complete.py`, injectable and not registered), run on the aux model (`UserDeps.aux_llm`) with thinking off.
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
`unlisted_tool_names` is the one published set the schema pass and the settings listing drop, and `sandbox_surface` computes the sandbox's narrower set where it is used.

### Budgets

The read budget is per document (`max_document_chars`), not a running total, since the host holds one decoded document at a time, and it is checked by size before decoding (`check_read_budget`) and by length after.
Inline `code`, the stored script, and every written file answer to the same cap.
`max_memory` bounds what a program retains, `max_feed_duration_secs` its bytecode, and `max_total_sleep_secs` its sleeping, while `max_wall_seconds` bounds the time it waits on host calls: past it a running call is cut off and every later one refused, as a `TimeoutError` the program catches to finish with what it has, so no timer has to stop it and lose what it paid for.
`request_timeout_seconds` is the pool's backstop for one worker turn.
The lifespan owns the worker pool (`sandbox.py`), and every call takes a fresh session, so no program sees another's state.

`HostCalls` applies one policy to every host function: `settings.sandbox.max_host_calls` caps a program's calls before the first await, a `ToolRetry` or bad argument becomes a `ValueError`, and any other failure a `RuntimeError` named by type alone, so no host details leak.
Monty's `max_suspensions` (`settings.sandbox.max_suspensions`) counts every round trip to the host, each file operation and name lookup too, so it sits well above `max_host_calls`, since its default of 1000 ends a program after about 500 file reads.
`UsageLimitExceeded` (`TURN_ENDING_ERRORS`) ends the turn however the program handled it.
Each call is kept as a bounded `HostCall` in `PythonResult.calls`, and a failed program's retry lists the first 20 before the traceback.

## Video and animated media

Vision chat models over the OpenAI Chat Completions protocol accept no video, so animated media is represented as at most `MAX_FRAMES` evenly sampled, downscaled, timestamped still frames.
Videos are sampled with OpenCV (`converters.video.sample_video`), whose wheel bundles an LGPL FFmpeg so no ffmpeg binary ships, and animated GIF and WebP with Pillow (`sample_animated_image`), then captioned by `asset_processing.caption_frames` on the aux model into the `<stem>.md` description.
`read_binary_document` attaches the same frames (tagged `<path>#t=<ts>s`) instead of the container bytes, while static images pass through verbatim.
Once pydantic-ai can send native video, the swap is local to `ReadBinaryDocumentTool._read_video` and `_read_animation`, `caption_frames`, and `converters/video.py`.
