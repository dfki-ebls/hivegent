# Hivegent Backend

The invariants this service is built on and the reasoning behind them.
The system shape is in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), the client half in [`../frontend/README.md`](../frontend/README.md).

`uv run pytest` is the whole test suite and needs no services: every test is stateless, so nothing reaches a live database and the paths that would hit PostgreSQL are stubbed with `monkeypatch`.
Migrations, the running server, and retrieval are covered by the dev stack and manual smoke tests instead.

## Interface language

The frontend sends the interface language as `Accept-Language`, and `LanguageMiddleware` makes it the ambient language of the whole request, streamed body and spawned background tasks included.
Every text a user reads (error details, job titles, chat titles) is a `Localized` value from `l10n.py` with one field per language, so a missing translation is a type error, and is resolved with `.current` where it is raised.
Text with parameters is a plain function returning `Localized[str]`, so its signature is declared and type checked once for every language.
Fixed text is an UPPER_CASE constant and text with parameters a snake_case function, both named after the condition or event they report (`_host_blocked`, `_too_many_images`) rather than their wording, with noun phrases kept for reusable fragments (`_documents`, `UNTITLED`) and the `_INSTRUCTIONS` suffix for prompt blocks.
Labels and descriptions of fixed option values such as pipelines are not localized here, the backend sends the enum values and the frontend catalog describes them.

What the model reads is split deliberately.
The agent's instructions and the notes injected as user turns are composed in the run's language (`UserDeps.language`, fixed once per run) and end with an explicit pin to answer in it, since what sits closest to generation decides the answer language.
Tool names, descriptions, parameter schemas, results, and retries stay English: models call tools most reliably from English schemas, smaller open-weight models lose accuracy on translated ones, and the schemas are not what pulls an answer into English.
The `EnglishToolCalls` guard runs every tool call in English, so a workspace message shared with the HTTP surface reaches the model in English while the same message reaches a user in their language.
A query argument instead says which language to phrase it in, since copying the user's language into a search over documents in another language is the common failure.
Features hold one capability per language around the same toolset rather than an instruction callable, which pydantic-ai would treat as dynamic and move behind the cache boundary.

## Database

### Writes are atomic

Repository functions create or update a row atomically, never as a read-then-write.
A `s.get(...)` (or `_find(...)`) followed by a conditional `s.add(...)` is a time-of-check-to-time-of-use race: two transactions both observe the row missing, both insert, and the loser's `IntegrityError` rolls back its whole transaction, silently dropping the document, conversation, or memory that triggered it.

Two PostgreSQL-native tools cover every case:

- `INSERT ... ON CONFLICT` for single rows.
  `ensure_row` in `db/_common.py` (`ON CONFLICT DO NOTHING`) materialises identity rows on first reference (`ensure_user`, `ensure_group`); `.on_conflict_do_update(index_elements=[...], set_={...})` overwrites columns (`db.documents.upsert_document`, `db.memory.save_memory`).
  A core upsert does not fire the ORM `onupdate=_now`, so bump `updated_at` in `set_` with `func.now()`.
- A transaction-scoped advisory lock (`pg_advisory_xact_lock`) for multi-row sequences no single constraint can guard, such as mirroring a conversation's messages into rows keyed by `idx`.
  See `db.conversations._lock`, taken by `append_branch` to serialise concurrent turns; it releases on commit or rollback.

The schema's constraints are the safety net, not the obstacle: keep the primary keys, the unique constraints, and `documents.single_owner`, and make the application cooperate with them atomically rather than race them.
The one accepted exception is a best-effort last-write-wins update with no constraint to violate, such as a throttled activity-timestamp bump.

### Schema and migrations

Alembic owns the schema, and the migration tree lives inside the package at `src/hivegent/migrations/`, so revisions ship with the wheel and the same files drive development and production.
Chunk metadata, text, and vectors all live in the `chunks` table and cascade with their document, so there is no separate vector index to reconcile.

`apply_migrations()` (`hivegent.db.migrations`) runs `alembic upgrade head` from the FastAPI lifespan in `server/app.py`, so every start brings the schema to head before accepting traffic and no deploy step is required.
The lifespan then verifies that the live `chunks.vector` dimension equals `settings.embedding.resolve_dimension()` and refuses to boot on mismatch, so an embedding-model change without a matching revision fails at startup rather than mid-request.

Revisions are written by hand, never autogenerated: `--autogenerate` needs a live PostgreSQL with `pgvector` to diff against, which is not available in every working environment.
Whenever you change `db/models.py`, add the matching revision under `migrations/versions/` in the same commit, following the `YYYYMMDD_HHMM_<revision>_<slug>.py` naming and structure of the existing ones.
Set `down_revision` to the current head, keep `upgrade()` and `downgrade()` symmetric, and name constraints exactly as the naming convention on `Base.metadata` produces them (`pk_users`, `fk_documents_owner_user_id_users`, `ck_application_settings_singleton`, ...), wrapped in `op.f(...)`.
The existing revisions demonstrate the dialect-specific bits: pgvector index ops, partial indexes, raw `op.execute(...)` backfills.

Verify against the dev database (the `services-flake` instance under `data/db/`, started by the dev shell or `nix run .#hivegent`) before committing:

```sh
cd backend
uv run alembic upgrade head
uv run alembic check   # confirms no drift between models and migrated schema
```

`hivegent migrate [<revision>]` wraps `alembic.command.upgrade` for ad-hoc operator use, and the standard `alembic` subcommands (`history`, `current`, `downgrade`, `stamp`, ...) work too, since Alembic discovers `[tool.alembic]` in `backend/pyproject.toml`.

In production the systemd unit from [`nixosModules.default`](../nix/nixos/service.nix) runs `hivegent serve` and the lifespan migrates during startup.
With `services.hivegent.postgresql.createLocally`, the unit is ordered after `postgresql.target`, so the role and database exist first.
A failed migration aborts startup with a non-zero exit code, which trips the unit's `Restart = "on-failure"` and surfaces in `journalctl -u hivegent`.

## Configuration

### Secrets are credential files

Settings layer init arguments over environment variables, `.env`, credential files, and the TOML file.
Credentials are files named like the environment variable they replace, for example `HIVEGENT_LLM__API_KEY`, in `$CREDENTIALS_DIRECTORY`.
systemd sets that directory for the unit's credentials, and the container image points it at `/run/secrets`, where Docker and Podman mount secrets, so one code path serves both.
On NixOS the unit imports every `HIVEGENT_*` credential from the system credential store: plain files in `/etc/credstore`, which systemd keeps readable by root only, or files encrypted with `systemd-creds encrypt` in `/etc/credstore.encrypted`.
A plain file is enough, `(umask 077; systemd-ask-password -n > /etc/credstore/HIVEGENT_LLM__API_KEY)`.
An encrypted credential carries its name and loads under no other, so encrypt it with `systemd-creds encrypt --name=HIVEGENT_LLM__API_KEY`.
`services.hivegent.credentials` loads one from elsewhere instead, from a path outside the Nix store or an encrypted file.
Unlike an environment file, a credential is readable only by the unit's user, stays out of the environment every subprocess inherits, and a missing `LoadCredential=` file fails the unit start.
Every secret setting is a `SecretStr`, so it stays masked in `repr`, logs, and tracebacks, and `config.reveal` unwraps it only where a client needs the value.

## Workspace content

### The filesystem is the source of truth

The tree under `data/workspace/<store_key>/` is authoritative for document content (markdown, originals, and assets), and the Postgres `documents` plus `chunks` rows are a derived index reconciled from it.
The single idempotent ingest path is `workspace.sync_entries_from_disk`, which folds a batch of entries back under one casebase lock.
It compares the entry's `content_digest` (`config.content_digest`) against the stored one and re-chunk only when the bytes changed, so a full re-derive is cheap.
A `(mtime, size)` stat fast-path (`documents.content_mtime_ns` / `content_size`, captured by `entries.ContentStat`) avoids even reading a description whose stat is unchanged: the stat is only a pre-filter, so a stat that moved without a content change (a `touch`, a checkout) costs one read and never a re-embed, and a stat that lied the other way cannot happen because the digest is re-checked whenever the stat differs.
The digest, `mtime`, and `size` are cleared together by `documents.upsert_document` and stamped together by `set_content_state` only once the chunks are durable, so a null digest always means "not indexed yet, re-derive".
A description with no prior row is ingested and stamped `origin = imported`, since its real provenance is not recoverable from disk.

`reconcile.reconcile_store` runs this ingest over every on-disk markdown first, then drops SQL rows whose description vanished.
It never deletes workspace files: they are the authoritative content, so a file without an owning entry is inert content rather than an orphan to prune.
Mutations should therefore go through the API, and what is dropped in by hand is picked up on the next boot.

### What becomes a document

`entries.is_description_file` and `entries.is_projectable_original` are the content-versus-document policy seam.
Markdown files are descriptions and become entries directly.
A non-markdown file becomes one when its markdown projection is a verbatim copy of its own text (a config, data-serialization, or source file): the ingest pass writes the missing `<stem>.md` and indexes it, so a file dropped in by hand is a real entry rather than content the search index cannot see.
Everything else stays inert on disk and is never chunked on its own — a binary because nothing textual can be derived from it, and a converter- or vision-backed format (`.csv`, `.html`) because that work has no place in a sweep that blocks the server from accepting traffic.
Both stay reachable: they are listed in the tree, can be deleted, and become entries as soon as they are uploaded or reconverted explicitly.
Deriving a description is idempotent and content-addressed, so it costs one decode on the boot that discovers the file and nothing afterwards.

"Verbatim" is not decided there: `converters.projection_for` is the one routing table saying which projection a filename gets (markdown, image, video, or convertible), and `workspace.prepare._prepare_upload` dispatches on the same table.
Three things have to agree about a file — what an upload makes of it, what the ingest pass may derive for it, and what kind of entry a row rebuilt from disk claims it is (`workspace.metadata._REDISCOVERED_KINDS`) — and asking one table is what stops them drifting.
Deriving the answer separately is how `.svg` ended up projected verbatim by one path and captioned by a vision model in another; the table now answers "markup", since a vision model cannot be shown an SVG (`VISION_MEDIA_TYPES` leaves it out) and its own text is the only index it can have.
The corollary is that deleting only an entry's `<stem>.md` by hand does not retire the entry when its original is a text file: the next ingest derives it again, and retiring an entry means deleting its files, which is what the delete API does.

### Paths

There is no working directory.
A document path is always full and prefixed by the workspace it names (`~` for the personal one, `@<group>` for a group), missing subdirectories are created, and a path naming no known root is refused with the roots named (`tools.base.workspace_root_hint`).
The grammar is stated once per surface — `WORKSPACE_PATH_INSTRUCTIONS` for an agent run, the FastMCP `instructions` for MCP — so no argument description repeats it.

It binds what comes back too: a message naming a path renders it through `workspace.paths._shown` (`store.scope.render` outside the package), since a store-local spelling names a path no tool and no route accepts.
The `workspace` mutations take the store rather than its directory wherever that is what it costs to say which workspace a path is in.
`store.route_path` (behind `workspace.Location.parse`) routes each path back to the workspace its prefix names, so a move's two ends may name different ones.
The mutating tools span the personal workspace plus the groups the user may write to; the read tools span every readable one.

A group is identified by the ID from its OIDC groups claim and nothing else: the `groups` row, `documents.owner_group_id`, the `group:<id>` directory, and the `@<id>` prefix all use it.
`auth.parse_group_claims` is the one place that knows how a provider spells that claim, and a display name is a client label only (`GroupInfo`, `User.group_labels`).

Path strings entering the system are folded to NFC by `config.normalize_unicode` (`sanitize_document_path` for HTTP, `tools.base.resolve_search_path` / `scope_paths` / `_glob_entries` for tool arguments), since macOS hands out decomposed filenames.
File content is never normalized, since `content_digest` fingerprints it verbatim.
Out-of-band files keep their on-disk spelling in `stem_path` until `workspace.normalize_workspace_paths` repairs disk and SQL in one idempotent pass, which `reconcile_store` runs before the ingest.

### Text, encodings, and what may be written

Every read of user-supplied bytes goes through `text.decode_bytes` / `text.read_text_file`, never `Path.read_text`.
The decoder handles BOM-marked Unicode and strict UTF-8 deterministically, falls back to CP1252 for legacy Western files, and returns `None` for binary-looking content.
Since the read tools, the upload pipeline, the reconciler, and the HTTP reads share that one seam, a hash taken by `read_document` still matches when the edit tools re-read the same file.
Writes are always UTF-8, so a legacy-encoded file is normalised the first time it is edited through the API, and a transcode is always reported (tool output, upload message, `convert_document` span) rather than applied silently.

That decoder is also half of what decides whether a document may be written, which keeps the read and write tools telling the model the same story.
Both sides ask the same two questions in the same order: `converters.vision_media_type` (is this shown to a vision model directly?) and then the decoder (do these bytes decode?).
`read_document` refuses on the first to hand the caller to `read_binary_document`, and `workspace.documents._current_text` refuses on it to say "upload a replacement", but the table is one table, so growing it cannot make one tool accept what the other rejects.
A refusal on either reader ends in `tools.base.sidecar_hint`, so a caller turned away is never sent to the other tool to be turned away again: an Office document is neither showable nor decodable, and both refusals name its `<stem>.md`.

So a document is writable exactly when it is readable as text, and the file's _name_ only decides how an allowed write lands:

- A markdown description is the indexed content itself, so `_rewrite_description` writes and re-indexes it in place under the casebase lock.
- Any other file is an original that `<stem>.md` is derived from, so `_rewrite_original` sends the new bytes through the same reserve → prepare → commit lifecycle an upload would use (`commit._phased_upload`).
  The projection is byte-for-byte the one uploading that file produces, stale assets are cleared with it, and a failed conversion leaves the previous entry intact.
  The read, the hash check, and the mutation all happen inside the reserve, under the lock, so `expected_hash` guards this path as it guards the other.

Creating a file asks the same question from the name alone, since a file that does not exist has no bytes to decode: `converters.writes_as_text` refuses a suffix on `converters.BINARY_SUFFIXES` and admits every other, so a `.csv`, an `.html`, or an `.svg` is created as the text it is.
That table is composed only from the ones that already name a binary format — what a vision model is shown, what is sent as bytes, what pandoc cannot read, what a query reads columnar — rather than from a list of every binary format there is, so a suffix no converter claims is not on it.
It is deliberately not `is_projectable_original`, which answers whether the _ingest pass_ may derive a projection by copying, and so refused every text format a converter claims (`.csv`, `.html`, `.tex`) while admitting binary ones no converter does (`.parquet`, `.xlsb`).
A new original must still claim a free stem, so a write can never silently supersede another entry's files, and a binary is replaced by uploading.

The refusal is one sentence (`converters.BINARY_WRITE_REASON`, the write-side counterpart of `text.NOT_TEXT_REASON`) shared by the gateway and by `tools.mutations.resolve_text_target`, which every text-writing surface resolves through.
That is what puts the refusal in front of the approval prompt rather than behind it, and the sandbox mount asks the same question (`writes_as_text`) the moment a program writes a new file, so a bad suffix fails the program where it stands rather than after it ran.

A whole write to a delimited suffix is checked against its own header by `tools.mutations.check_delimited_rows`, since a table built a row at a time is one f-string away from a row of the wrong width, after which every value on it lands under the wrong heading with nothing downstream to report it.
The header decides the width and `converters.DELIMITERS` decides the separator, one table read by both the write gate and `query_table`'s loader, so a `.csv` is comma-separated by the name it was given and the surface that writes one can never disagree with the one that reads it back.
An edit replaces a string inside a file it did not build and is left alone.

### A conversation's `/tmp`

`/tmp` holds a conversation's working state, content that is never a document: intermediates, the state a later `run_python` call picks up, a program written only to be run, a tool result too large to show whole.
Each conversation owns one folder, `<data_dir>/tmp/<conversation_id>/`, outside every workspace, and every agent surface spells it `/tmp`, the way a container session has one `/tmp` for its lifetime.
There is no user level: conversation ids are server-generated keys and the `conversations` row names the one user a folder belongs to, so ownership is read from the database and never from the path.
`tmp.tmp_dir(data_dir, conversation_id)` is the one place an id becomes a path, and it refuses an id that is no safe path segment.

`/tmp` is a root beside the workspace roots rather than a corner of one: `UserDeps.search_paths` appends `tmp.tmp_search_path`, a `SearchPath` whose `PrefixScope("/tmp")` renders and strips the prefix (the same `tools.scope.PrefixScope` behind `WorkspaceScope`), so `read_document`, `grep`, `glob_documents`, `list_documents`, `jq`, `query_table`, and `read_binary_document` reach it with no code of their own, a `..` out of it resolves outside the root and is refused, and a conversation can never name another's folder.
Nothing about a workspace applies to it: it is never reconciled, indexed, or searched, carries no `DocumentFilter`, and announces nothing, so the workspace code knows it only as a folder root of the gateway, never by name, and no name in a workspace is reserved for it.
`UserDeps.conversation_id` is set by `build_run_prefix`, which also creates the folder, and a subagent inherits it, so `explore` shares its parent's `/tmp`.
A caller without a conversation, MCP and the admin tool console, gets no such root, so a `/tmp` path is refused there like any path naming no root, with the roots it does have named.

How a change is asked about is a property of the root, not of the caller: every `SearchPath` carries a `CommitPolicy`, `Gated` for a workspace (behind the approval the mode asks for) or `Direct(reserved)` for `/tmp` (written straight into the folder, with no approval).
The policy also names what no ordinary change may touch (`CommitPolicy.reserves`), each `.assets` payload of a gated root and the `reserved` folder of a direct one, which `SearchPath.is_reserved` asks for a program's overlay and the gateway's planner for every path a change names.
How a change lands is the same for both: `/tmp` is a `workspace.operations.Folder` (`tmp.tmp_root`) carrying its search path's `Direct`, a root of the one changeset gateway beside the casebases (see [Mutations](#mutations)), so final-state occupancy, chains and swaps, a write following its move, parent and basis checks under the lock, parking, and rollback exist once, and a hash check, a write mode, and an edit refuse in the gateway's words.
Every surface that writes asks the root rather than the path: the agent's gateway maps each of `UserDeps.writable_paths` to its casebase or folder with its filter, so a mode narrows the writable roots in one place, and whether to ask, or what a program stages, is decided per item from the root it routes to (`Gateway.gated`), while the overlay of a program asks the same policy (`WorkspaceOS.policy`).
So `write_document`, `edit_document`, `move_documents`, `delete_documents`, a saved tool result, and a program all reach `/tmp` the same way, their validators ask nothing for a direct change, and `/tmp` is writable in every mode, since it is the conversation's own: `UserDeps.writable_paths` leaves the workspaces out in a mode that may not write them, and keeps `/tmp`.
The `write` feature is therefore offered in every mode, so a read-only run can still store and edit a script it reruns, while its workspace guidance (`WRITE_INSTRUCTIONS`) is a shared block narrowed to the modes that may write the workspaces and `apply_changes` is withheld outside them by its `prepare` hook, since no such run stages anything.
A move between a folder and another root is refused by the gateway, since no one commit can carry a file between them, and is a write of the text and a removal in a program, while a rename within `/tmp` is a move that carries a directory or a binary as it is.

`tmp.max_bytes` caps one folder through its `workspace.operations.Quota`, the root's own hook on the gateway.
The planner puts to it what a changeset adds to the folder, a write's text less what it replaces and a delete's tree once, and it walks the whole folder only when the change grows it, so a refused change writes nothing and a folder already over the cap may still shrink.
`GET /api/documents/{filepath}` reads a `/tmp` path when the request names its `conversation_id`, after checking in the database that the user owns that conversation, and resolves it with the tools' own containment checks, so the chat shows a temporary script's source or image the way it shows a document's.
A folder ends three ways: deleting its conversation deletes it, `DELETE /api/tmp` ("Clear Temporary Files", which counts the files it removes) and `DELETE /api/user-data` delete the folders of every conversation the database lists for the user, and `tmp.sweep_tmp` deletes one untouched for `tmp.ttl_hours` (judged by the newest mtime anywhere in it, stopping at the first recent one) or one naming no conversation row, looking up only the idle folders' ids.
The sweep runs in a background task the lifespan starts after the migrations, first at once and then every `tmp.sweep_interval_hours`, beside the staged changeset prune, so the boot waits on neither.
An orphan is kept for one sweep interval, since a new conversation's row is only written when its first turn ends, and the orphan rule is what catches a user deletion's cascade and a crash.
A compacted conversation starts with a copy of its source's folder (`copy_tmp`), since its summary may name what the source kept there.

`TMP_INSTRUCTIONS` is shared between the `explore`, `python`, `web`, and `write` features, and `PYTHON_INSTRUCTIONS` names `/tmp` as the home of a rerunnable `.py`, since a `.py` in a workspace is an original and gets chunked.
The mutation receipt says the rest at the one moment the path is in hand: it points a `/tmp` `.py` at `run_python`'s `script_path`.
That pointer is a `MutationHint` the write and edit tools take like `filter_func`, injected by `agents/tools/write.py` alone, since the MCP surface writes through the same tools and has no `run_python`.

- TODO(binary): support binary files in `/tmp`, so any name and suffix is accepted, the workspace-only rules (text suffixes, excluded folder names, `DocumentFilter`) do not apply there, and `run_python` reads and writes bytes (`read_bytes`, `write_bytes`, binary `open` modes) under a per-file byte cap.
- TODO(binary): support binary files in the workspace, so a binary written or moved there by `run_python` (or later the shell) becomes a staged changeset item that runs the upload conversion pipeline at apply time, and the approval summary names it as an upload with its size and type.

### Notifications

Every workspace change reaches the client on the per-owner SSE feed, which the frontend turns into a refresh of the named scope.
Long-running work is a real job and its settled `document.*` event carries it.
A mutation that ran inline must publish through `workspace_events`, or every client but the one that asked stays stale: `notify_workspace_change` sends a bare `ScopeChanged` for the store it holds, and `announce_commit`, which `apply_changeset` calls for the `owner` it is given, sends one `ChangesetCommitted` per commit.

That event names the scopes the commit changed, taken from its roots, and what it moved and deleted, the `changes.PathChanges` that `apply_changeset` also returns in its `workspace.AppliedChangeset`.
It is the one channel a client follows moves and deletes through, whoever made them, the agent included, so a path the client keeps, such as the chat's document filter, never goes stale.
One event per commit lets a client apply a swap across two workspaces at once.
Example: `{"type": "changeset-committed", "scopes": ["~"], "moves": [{"source": "~/a.md", "destination": "~/archive/a.md", "is_dir": false, "replaces": false}], "deletes": ["~/b.md", "~/b.pdf"]}`.

The notification skips the `X-Client-Id` that caused it, so the asking tab keeps its own read-after-write and the others learn from the feed.
The asking tab learns what changed from its response instead, which is why the change routes return the same `PathChanges`, and since the SPA always sends its id, it hears of each commit once.
`workspace.Gateway`, which the agent, the MCP surface, and the host commit through, skips no client, so the tab whose chat runs the agent hears of its changes on the feed like every other and no tool result has to carry them a second way.
Delivery is per-owner, so a group workspace refreshes for the writer and not yet for the other members, who neither refresh nor follow the moves.
Both events are transient and never retained, so the client re-reads every scope it holds on each handshake (`onFeedReady`).

### Preparing for a read-write shell tool

A future shell tool will let the agent run native commands such as `ls`, `cat`, `find`, `grep`, and editors that modify files directly, bypassing the structured mutation gateway inside an isolated session.
The live filesystem and its SQL index stay unchanged while commands run, and an explicit checkpoint folds an approved session diff back into both before notifying clients.
None of the pieces below require breaking changes to the code above.

- TODO(shell): put arbitrary command execution behind a dedicated sandbox runner with a narrow session API rather than adding a shell or container-runtime socket to the backend, so the runner can select an OCI sandbox, `systemd-nspawn`, or a stronger runtime without changing the agent tool.
- TODO(shell): sandbox each session as an unprivileged user that sees two mounts and nothing else: the store's working copy at `/workspace`, and the conversation's folder (`tmp.tmp_dir`) read-write at `/tmp`, with no backend secrets or service sockets, no capabilities or devices, a read-only root, and explicit network, CPU, memory, process, duration, output, and disk limits, the last of them `tmp.max_bytes` for `/tmp`. `subprocesses.run` is unsandboxed and is safe only for the fixed-argument tools (`rg`, `jq`, `pandoc`).
- TODO(shell): run each session against an isolated working copy or overlay of `/workspace` so the casebase lock is never held while commands run, with a copy-based directory or Linux VM for macOS development because overlayfs and `systemd-nspawn` are Linux-only. `/tmp` needs neither, since it is written directly and asks no approval, exactly as the Python sandbox writes it.
- TODO(shell): compute the `/workspace` session diff in trusted runner code, surface it for approval, and make checkpoints explicit so a session can fold back several times or discard every pending change for free.
- TODO(shell): fold the session diff back through `workspace.apply_changeset`, which already prepares converter-backed formats outside the lock, takes every involved casebase lock, rejects changes whose starting basis moved, applies files and rows atomically, marks written entries in flight while they index, and only then notifies clients. The mapping of every created, changed, renamed, and deleted path to one changeset item with its basis already exists for the Python sandbox (`tools.changeset.stage_changes`, see [Writes from a program](#writes-from-a-program)), so a shell session diff can reuse its entry rules.
- TODO(shell): keep an entry whose synchronization failed out of retrieval rather than serving stale chunks, retain its dirty state for retry, and report the checkpoint as unsettled until every affected indexable entry succeeds.
- TODO(shell): decide which shell-created binaries and converter-backed formats become entries at fold-back. A plain-text file can use `is_projectable_original`, a changed existing original must refresh its projection, and a new `.pdf` or `.csv` may either run the upload conversion pipeline or remain explicitly inert.
- TODO(shell): keep all workspace access behind `Casebase.workspace_dir(data_dir)` and all `/tmp` access behind `tmp.tmp_dir(data_dir, conversation_id)`, so each mount's root can be injected in one place.

## Conversations

### A server-authoritative message tree

The database is the source of truth for chat history, not the browser.
A conversation's messages form a tree: every `Message` row has a global `id` and a nullable `parent_id`.
The active branch is simply the newest one, so no branch pointer is stored: the linear history the frontend sees is the _active path_, the conversation's most recently created message walked up to the root via `parent_id` (`_load_active_path` in `db/conversations.py`).
Because references then flow only `messages -> conversations`, there is no FK cycle to break with a deferrable pointer and no write path to trust with keeping a pointer valid; the `(conversation_id, created_at)` index serves both the newest-leaf lookup and the cheap `EXISTS` check that drops empty conversations from the sidebar.
No per-conversation message count is stored or shown: the active-path count is a path aggregate (a plain `COUNT(*)` would include sibling branches), so materializing it would mean an immutable per-node counter or a per-row tree walk, neither worth carrying for a sidebar label.

Each turn the client sends only the new message plus the operation (`trigger` and `messageId`), never the full history.
`_run_chat` calls `resolve_fork` to load the active-path prefix up to a fork point and replays it as the run's `message_history`; `run_and_persist` persists the run's message list on every finish, and the route's persist closure appends only the new tail (`captured[len(prefix):]`) as a chain under the fork point (`append_branch`).
A plain submit forks at the current leaf, editing a message forks at its parent, and regenerating forks at the nearest user turn, so the prior branch is preserved as a sibling rather than overwritten.
The newly appended chain is the newest message, so it automatically becomes the active path and forking never touches a pointer.
`GET /conversations/{id}/messages` projects the active path with `dump_messages_with_ids`, which anchors each `UIMessage.id` to its tree-node id and annotates forking nodes with branch metadata for the branch-navigation UI (see `../frontend/README.md`).

A reload hands the client addressable ids, but a message the client just sent carries only the id its SDK minted locally, which names no node.
So `_run_chat` reserves that node id before the run (`append_branch(head_id=...)`) and returns it in the `X-Message-Id` response header for the client to adopt, exactly as the first turn of a new conversation adopts `X-Conversation-Id`.
Without it, editing a message sent earlier in the same session would resolve to no fork point and silently continue the conversation instead of forking at it.
Only a request carrying a user prompt reserves an id, and `resolve_fork` treats a `messageId` as an edit only when it names a user turn on the active path, since the AI SDK also sends `messageId` when it auto-continues after a tool approval, where it names the assistant message that requested one.

That auto-continuation is also why `ChatAdapter` keeps only the client's _user_ messages (`messages` in `server/vercel.py`).
The base adapter appends whatever the request carries on top of the caller's `message_history`, and the SDK's approval resend is the assistant message holding the pending tool call, already the last message of the replayed prefix.
Appending it would put the same `tool_call_id` in the history twice: pydantic-ai closes the stored copy with a synthetic "interrupted" return and gives only the echo the real result, so the model sees its call interrupted and reissues it.
The decision itself is unaffected, since `ChatAdapter.approval_decisions` reads it from the request rather than from the loaded messages.

An approval the user never answers is closed by the next turn (`decline_pending_approvals`).
A run that ends awaiting one leaves its call dangling on purpose so a later request can carry the decision and resume it; a request that carries a new prompt instead ends that run for good.
Left in the stored history the dangling call is not inert: pydantic-ai repairs it on the way to the provider with a generic "interrupted" result, the model reads that as a transient failure and reissues the identical call, and one abandoned approval is enough to have every later turn repeat it.
The refusal is stored as its own node before the run, which both states what happened and makes the delta's head the new user message, so the node id announced in `X-Message-Id` still lands on it.

Because history is loaded from SQL, there is no browser round-trip to strip `ToolReturnPart.metadata`, and the client-trust surface shrinks from the whole conversation to one new message.
Persistence is therefore hard-fail: a failed write surfaces as a trailing error chunk on a clean drain, and is only logged on a client disconnect.

`run_and_persist` persists exactly what `capture_run_messages()` holds, with no reconstruction, so on pydantic-ai v2 an answer cut off mid-stream is persisted by the same tail with no code of its own.
A turn that never finished is normalized first: `close_orphan_tool_calls` closes the call the error aborted (an unresolved call would reload as an approval request and make the history invalid to replay), and `record_turn_error` keeps the error text for the reloaded banner.
A clean finish is stored untouched, so a genuinely approval-pending call stays dangling and reloads as the approval request it is.

### Client-posted messages are validated strictly

The Vercel AI request models forbid extra fields, so a part the AI SDK builds but pydantic-ai has not transcribed rejects the whole request with a 422 before the run starts.
That takes down every endpoint the browser hands messages to at once: the chat route on an approval continuation and the import route on an archive exported mid-session.
Such drift is an upstream bug and is fixed there rather than worked around here (the `id` on the adapter's own `reasoning-start` chunk, which its `ReasoningUIPart` then refused, needs pydantic-ai >= 2.34), so the floor is the lockfile and `tests/unit/test_client_messages.py` pins the shape a real client posts.

Nothing may relax that strictness to accommodate a client: `extra='forbid'` is what stops a malformed `approval` object from re-matching the unanswered variant and releasing a gated call.

### Tool approvals round-trip symmetrically

A decision's `reason` is the user's optional note, and the model-facing text is worded here rather than in the browser, in English like every tool result.
`ChatAdapter.deferred_tool_results` turns a denial into `ToolDenied(denial_message(note))` and hands an approval's note to its call as `DeferredToolResults.metadata`, which pydantic-ai exposes as `RunContext.tool_call_metadata`.
`agents.approval.ApprovalNotes` appends it to that call's return, so the note is part of the stored result the continuation and every later turn replay, with no extra user turn in the history.

An approved call just runs and stores an ordinary successful return, so nothing in the message list would say it was ever gated, and a denial stores the refusal rather than the note the user typed.
`vercel.record_approvals` stamps each decision with its note on the metadata of the request carrying the returns, and `dump_messages_with_ids` puts it back on the projected part, overwriting the refusal text the projection copied into `reason`, so `approval.reason` always means the user's note.
Every denial is recorded that one way, an abandoned one too (below), so the projection overwrites unconditionally.
The note is appended as one more item of the return value's content list (a plain return becomes its first item, and the client shows a list item by item) rather than as `ToolReturn.content`, which pydantic-ai sends as a separate `UserPromptPart` that would be stored and shown as a user turn nobody typed, and it is appended after `ToolOutputSpill` bounded the return, which `ApprovalNotes.get_ordering` declares through pydantic-ai's capability ordering (`wraps=[ToolOutputSpill]`) rather than leaving it to list order.
pydantic-ai sorts the agent's capabilities and the run's as one list, so the declaration holds with the spill on the agent and the note on the run.

That metadata is UI-owned and never sent to the provider, like the reasoning durations and the turn error beside it.
It rides on the request node rather than the closer `ToolReturnPart.metadata` because that field is already the tool-output chunk channel (`tools.pydantic_ai.wrap_tool_output`).

The request side travels the same way: the protocol's approval request chunk carries ids only, so `ChatEventStream.handle_run_result` stores what each `ApprovalRequired` carried on the response that asked, under `approvalMetadata` keyed by tool call id.
The run's one message metadata chunk delivers it live and the stored response projects it again on reload, so the prompt shows the same server-resolved paths either way.

An approval the user overtook by sending another message is a denial on both sides: `vercel.decline_pending_approvals` closes the dangling call in the history on the next request and records the decision, denied with no note, the way `record_approvals` records a user's, and `chat-utils.declineAbandonedApprovals` derives the same denial over the live transcript, since only the last message can hold a request that is still answerable.
So the buttons disappear the moment the prompt is sent rather than on the reload that would have shown them settled.

### Compaction is a turn of the conversation

`summarize_conversation` asks for a summary by appending `COMPACT_PROMPT` to the conversation's own message list and running it under the same `RunPrefix` the chat turns ran under.
Everything ahead of the prompt is then byte-identical to the request the provider just served, so its KV prefix cache answers for it and only the prompt is prefilled; the request it replaced re-rendered the history as a flat transcript under different instructions and no tools, which shares nothing with that cache and pays a full prefill of a nearly-full window.
That is what `AgentRunConfig` exists for: the compaction route posts the run configuration a chat turn posts, and `build_run_prefix` composes both, owning the two preconditions of an identical prefix (the MCP list is validated and `llm` resolved before the toolsets are built).
The other half of that prefix is the message list, read where the chat route reads it (`compact_conversation` calls `load_conversation`) rather than accepted from the browser, which also spares the wire a near-full context window on the one request that exists because a near-full context window stopped fitting.

The continuation is the _larger_ request and is asked for exactly when a conversation has stopped fitting, so room is made by dropping messages from the **tail**.
That is the whole reason there is one summarizer and not two: cutting the head would throw away the block being reused, while cutting the tail leaves a shorter prefix of that same block, so a trimmed continuation is still a cache hit.
Where to cut comes from the conversation itself: `ModelResponse.usage` records what each request carried, so `total_tokens` on a response is exactly the size of the history up to it — observed, not configured, which is why `_plan` needs no tokenizer, no model card, and no `context_window` setting.
This is the measurement that only survives in SQL.

`_cut_points` offers only prefixes in which every tool call has been answered, since a provider rejects a history ending on an open one, and not those ending on a fresh user prompt, which measure the same as the turn before them while keeping a message the provider never counted.
That makes the longest prefix the longest one the endpoint has actually _served_, which is where the first attempt starts: the whole history when the last turn succeeded, everything but the refused prompt when it did not.
The endpoint is the cheaper judge of whether that fits, since an overflow is refused before anything is prefilled while a prefix trimmed on suspicion spends a full request to answer from less of the conversation than was available, so only a refusal sheds.
That rule is also what lets the subagent recovery path (`_safe_summarize`) call this summarizer instead of a flat-transcript one of its own: a crashed subagent's messages may end mid tool call, and trimming back to the last answered one is both what makes the request legal and what makes it fit.

The compaction reserve is the summary request's own resolved completion cap plus `_COMPACT_PROMPT_RESERVE_TOKENS`, taken off the `ModelSettings` the request will actually run under rather than off `SUMMARY_MAX_TOKENS`, which a lower configured `max_tokens` clamps.
Each refusal sheds that reserve, walking the summary back a turn at a time rather than halving it (`_MAX_ATTEMPTS`, three); a conversation nothing ever reported a size for plans one attempt with everything.
One `RunPrefix` carries the resolved model alongside the config it came from, so `summarize_conversation` takes no model of its own and cannot be handed one the prefix disagrees with.

`SUMMARY_MAX_TOKENS` is 8192, between opencode's 4096 and pi's 80% of a 16384-token reserve, and clamped to the request's own `max_tokens` where that is lower.
`COMPACT_PROMPT` bounds the summary by structure rather than by a word count, which is what both harnesses settled on: a length cap makes the model drop sections instead of tightening them, and the summary is the only carrier of what the conversation established.
`_COMPACT_LIMITS` keeps the request budget to one and the tool call budget to zero.

Compaction is offered when the provider has actually refused a turn, never on a forecast.
`ContextLimitBanner` keys off `is_context_overflow`, the same rejection the chat route already classifies, so the trigger is an observed fact.
Forecasting would take three numbers the system does not reliably have — a window (a self-hosted model card states one only sometimes, and the genai-prices table covers hosted models only), a reserve the continuation needs, and a share of the window to fire at — and being wrong either way is worse than being late.

TODO: adopt pydantic-ai's `CompactionPart` as an in-conversation boundary instead of forking a new conversation.
Upstream models compaction as a part inside one message list: `post_compaction_window` trims everything before it, and derived state resets at it for free.
That would keep one conversation id across a compaction (today `create_compacted_conversation` mints a new one and the client navigates, which is why compaction cannot auto-fire without teleporting the user mid-flow).
Blocked on transport: `OpenAICompaction` refuses anything but `OpenAIResponsesModel` and `CompactionPart` round-trips provider-owned data, so neither reaches an `OpenAIChatModel` against vLLM or llama.cpp (upstream #7255).

### One budget per turn

`turn_usage_limits` bounds a chat turn, and a subagent runs on the parent's `ctx.usage` under the parent's `ctx.usage_limits` and `ctx.conversation_id`, so the main agent and every subagent spend one count and a trace groups the delegated run with its turn.
`IterationLimitWarner` reads the same `ctx.usage_limits`, so its wrap-up note names the limit that will actually stop the run, and a run without a request limit is never warned.
Each purpose runs its own named agent (`chat`, `explore`, `summarize`, `title`, and `complete` behind `llm.complete`), since the agent name is what Logfire attributes a run's spend to.

### Export, citations, and attachments

A conversation export is a `ConversationArchive` with two halves, since the conversation exists in two places that can disagree.
`backend` is the persisted active path plus the system prompts each turn actually ran under, read back off `ModelRequest.instructions` rather than recomposed, because recomposing would describe today's settings and because the Vercel AI stream never carries instructions to the browser.
Snapshots collapse while the prompt is unchanged, so narrowing the document scope mid-conversation stays legible.
`frontend` is what the tab held, including a turn that errored before it was persisted, which is the case the export was built for.
Import restores `backend` when it has messages and falls back to `frontend`, and the archived prompts are a record that is never replayed, since the imported conversation runs under the importing user's settings.

Citation line chips show only evidence captured in persisted read, grep, or search tool outputs from the conversation.
The document name opens the current workspace path separately and never applies a historical line anchor to current content.
Tool call IDs keep repeated reads distinct, multiple captured versions are presented independently instead of guessing which one a citation meant, and a line chip with no supporting tool output is disabled.

A chat turn attaches images and nothing else, gated on `converters.INGESTIBLE_IMAGE_MEDIA_TYPES`, the media types every vision backend ingests identically, so no `BinaryContentMode` policy and no conversion runs on the chat's latency budget.
Anything else belongs in a workspace, whose upload pipeline converts, chunks, and indexes it once for retrieval rather than spending context on it every turn, since an attachment is stored once but re-sent to the model on each later turn.
`/settings` serves the same table and the size cap to the client as `AttachmentLimits`, so the file picker refuses what the chat route would refuse.

## Document tools

The tools under `tools/` must not read `hivegent.config.settings`: every tunable is a dataclass field with a default, applied where the tool is instantiated (`agents/tools/*`, `mcp/tools/*`).
Importing sibling infrastructure (`converters`, `chunkers`, `subprocesses`, `security`) is fine.

The read tools serve the markdown projection, which is what retrieval and citation line anchors are built on.
Two document shapes are deliberately not served that way, and `converters.TABULAR_SUFFIXES` and `converters.JSON_SUFFIXES` are the one table behind each split, for the same reason `VISION_MEDIA_TYPES` is the one table behind read/read_binary: the specialised tool gates on it and `read_document` points at that tool, so the reader cannot silently spend the context on a document that could have been queried.

### Read tools are list-first

A read tool takes a list of items rather than one (`reads`, `files`, `file_paths`, `urls`, `searches`, `queries`, `patterns`), and a one-item list is how a single item is asked for, so no tool has a singular twin and the schema the model reads is the same either way.
Batching saves the round-trip a parallel tool call costs a model that emits one call per step, and lets a call share work across its items: `search` resolves its storage, filter, and reranker once and fuses the rankings by reciprocal rank, since cbrkit normalises each query's scores on its own, and `query_table` loads and retypes its files once for every query.
`tools.base.run_batch` is the one contract behind the per-item tools: items are deduplicated by key and served in request order with bounded concurrency, each renders under a `==> key <==` header within its `BatchShare` of the call's budgets, and a `ToolRetry` from one item becomes its `ItemFailure` in place.
Only a batch in which every item failed is refused, in the item's own words when there was one, so a one-item list behaves exactly as a scalar call did.
The adapters accept a bare item where a list is declared (`accept_scalar`), since models often send one, while the published schema still asks for the list.
`grep` and `list_documents` stay single: one pattern or directory already spans every file.

### Scope and filters

The chat's document selection is asymmetric.
`included_documents` is advisory and only named in the prompt (`parse_document_scope`, `format_document_scope`), never a filter, so a run can follow a reference out of the selection.
Only `excluded_documents` becomes a `DocumentFilter`, enforced by the path tools through `SearchPath.filter_func` and by retrieval in `resolve_accessible_document_ids`, staying one predicate.
A directory move would carry what the filter hides and change the paths it matches, so the gateway's planner refuses one where it locates the source when `PathFilter.hides_within` finds an excluded entry inside it, asked of the entries rather than a walk of the tree, under the locks of the commit.
The filters are the ones `workspace.Gateway` is given, the run's for an agent change and the approver's current ones for a staged changeset applied later.
The HTTP changes route passes none, since the filter belongs to a run and the user sees everything, and a directory hidden by name, such as `node_modules`, stays hidden wherever it moves.

A filter argument naming a directory that no accessible root holds is refused, never answered with an empty result, since a listing that silently comes back empty reads as an empty workspace and the caller cannot tell that from its own typo.
`tools.base.resolve_directory` folds the directory and pairs it with the roots that hold it, `missing_directory_retry` is the one refusal, and both reach `list_documents` and `glob_documents` through their `path`.
It is not `workspace_root_hint`, which tells a _file_ path to lead with a root: a filter argument may legitimately carry no prefix, so the refusal names the roots without demanding one, and it names the caller's own spelling rather than the prefix-stripped remainder, which would staple a second prefix onto a path that already carries one.
The roots come back resolved, so the walk starts at the subtree instead of sweeping the workspace and discarding the rest.

### Mutations

Every write, edit, move, delete, and new directory is an item of one `changes.Changeset`, and `workspace.changeset` is the one gateway that commits it, the HTTP routes included.
The operations are generic over the location type, a canonical `str` where a tool or a staged program spells a path and a `workspace.Location` once `workspace.route` sent it to its casebase, so the tools build them without reaching the workspace.
The items apply at once rather than in order, the way a diff of two states does: every source and basis names the workspace as it is now, every destination and write target the workspace as it ends up.
An ordered list of operations had to refuse what no order expresses, a swap or a rotation, and every overlap besides two hand-picked orders, while the sandbox overlay already is a diff of two states and had to be squeezed into one.

`plan_changeset` resolves every source against the disk into units, an entry with its description, original, and assets, or a directory as a whole tree, and maps each to where it ends up: a moved tree carries what lies below it unless a nested item moves or deletes that part on its own.
It then checks the final state instead of an order: each unit is claimed by one item at most (nesting aside), each final path is filled by one item at most (a text write of an original fills its projection too), every destination is free or vacated by the changeset, so a move onto an occupied path needs a delete of it in the same changeset, no final path lies below a file another item writes, and a new original's stem holds no other entry once everything applied, so deleting an entry frees its stem for a new original in the same changeset while moving one onto it does not.
A write target resolves through that mapping to the file whose content it replaces, which is how a write follows the move that carries its file, and a destination naming a directory that stays means into it, like `mv`.
So chains, swaps, rotations, an overwrite, moves and deletes inside a moved directory, and case-only renames all fit in one changeset.
A `Delete` may state the kind it `expect`s, so a folder sent as a document is refused under the lock rather than checked before it.
The plan reads the disk in worker threads and the rows of every source in one query, and returns the resolved changeset with the `changes.ChangesetSummary` an approval shows, built from the effects it resolved: creates and updates with a capped diff, moves with their kind, deletes with every companion, new directories.

`apply_changeset` prepares converter-backed originals without a lock, planning first only when an item writes text in a workspace, takes the locks of every involved root in `store_key` order, resolves again (deriving every text item and checking every basis, path, and quota under the locks) and refuses an original whose conversion went stale meanwhile, and installs every file change through one rename journal (`paths._Journal`) in a worker thread and every row change in one transaction (`db.engine.session`, which the repository writes join through their `s` argument), so a failure in either restores the files and rolls back the rows.
Deleted and moved units leave first, deepest first, a moved one parked in the staging area with its rows on a temporary stem under `.changeset-park/`, which exists only inside the transaction and so never meets a listing, the index, or reconcile, then the moved ones land, shallowest first, then the writes and new directories follow.
Parking is what lets a swap pass through the `(owner, stem_path)` unique constraint, and rows are only updated, never recreated, so a moved document keeps its id and its embeddings.
It indexes after releasing the locks, the entries concurrently and each entry's assets before it, and announces the commit once.

A `workspace.Location` names a `workspace.operations.Root` with a path local to it, and the root answers for the rules its changes follow, so the planner and executor never ask which kind they hold.
It has a `path`, a `scope`, a `policy` naming what it reserves, a `quota` (`None` for a casebase), and a `store`, the casebase whose rows, index, in-flight claims, and announcements its changes carry.
A `store.Casebase` is its own store, and a `workspace.operations.Folder` of plain files written directly, of which `/tmp` is the one, has none, so its file is a unit without an entry, which the path logic already handles like a tree.
A root without a store skips the row fetch, the in-flight guards, originals and their projections, the slot check, row changes, indexing, and announcements, and a move to or from it is refused, since no one commit can carry a file between it and another root.
What a changeset adds to a root with a quota is put to it, and the files it evicts leave with the deletes, which only the host saving a tool result may ask for (`Gateway.host`), as only the host may write what the root reserves.
Every root's lock comes from one registry by `store_key` (`locks._lock_of`, held weakly so it lives only while a change uses it), taken in that order, so a folder goes through the same async `_commit` as a workspace.
A commit stages beside every root it changes (`_staging_root`), the data directory for a batch spanning a workspace and `/tmp`, so every step stays a rename, and the staging area is created by the worker thread installing the files and removed in one once the locks are released.
The summary leaves a folder's items out, since only a workspace change is put to a person, and so does the `PathChanges` a commit returns and announces, since no client tracks a folder's paths.
Both come from one reading of the resolved items (`_path_changes`), taken in the worker thread that resolves them under the locks, before anything moves: an entry's description and original each, a directory as a whole, every destination as it resolved.

The HTTP API applies every move, delete, and new directory through `POST /api/changes`, whose body is a list of operations (`server.models.ChangesRequest`) applied as one changeset within the request rather than as a job: a move or delete only renames files and updates rows, so nothing is left to index.
For clients that change one item at a time, `POST /documents/move/{path}`, `DELETE /documents/{path}`, `POST /directories`, `POST /directories/move`, and `DELETE /directories` are shortcuts that build exactly one such operation and share its code path (`_apply` in `server/routes/documents.py`), so they behave and refuse exactly as the batch route does.
Its items carry canonical paths, each resolved with write access, and mirror the gateway's operations without a basis, while a delete must name the kind it `expect`s so a client never removes a folder by accident.
So a single item and a selection go the same way, the batch lands whole or not at all, swaps and chains in it work, and one refusal rejects it with the gateway's message, which names the canonical path it refuses.
Each answers with the `PathChanges` it applied (`{"moves", "deletes"}`), since the asking tab's feed skips the change and what it sent is not what applied: a move into an existing folder lands below it, and an entry moves and goes with its original.
Whatever carries content or starts a job keeps its own route: uploads, writes, asset descriptions, rechunk and reconvert, and wiping a scope.

Paths compare the way the workspace's filesystem compares them.
`entries.folds_case` probes each filesystem once, by device, and on a case-insensitive filesystem (macOS) claims compare by `entries.path_key`, an existing path is respelled the way the disk spells it (`entries.respell`), so `A.md` and `a.md` are one file with one row, and only a destination's last segment keeps its spelling, which is how a case-only rename renames.
The tools respell in `tools.base.canonical_local_path`, before any filter is asked, so a hidden `secret.md` cannot be reached as `SECRET.md`, and the sandbox mount resolves through the same function.
The changeset is plain data that `pydantic.TypeAdapter` round-trips, and every item may carry a basis, a content hash or an `entries.ContentStat`, which is the contract the sandbox overlay feeds in.
The stat carries the inode beside `(mtime, size)`, in the reconcile fast path too, since a file replaced by another of the same size with its mtime preserved is the one change those two miss, while every in-place write and rename keeps the inode.

The agent's mutation tools are list-first: `move_documents` takes `moves`, `delete_documents` takes `paths`, `edit_document` takes a list of `edits` applied in order as one write, and `write_document` stays one file per call.
No argument is a glob, so what the user approves is exactly the paths that change.
The tools build their changeset with the builder their validator calls too (`tools.mutations.write_changeset` and its siblings), hand it to one `Commit` callback, and turn a refusal into a correction through `mutation_errors`.
`workspace.Gateway` binds the gateway to the roots a surface may write, the filters it sees them through, and the user it tells, so the agent, the MCP surface, and the host saving a tool result supply only those.
Each validator in `agents/tools/write.py` goes through one `_gate`, which asks when any item routes to a workspace and only then plans the whole changeset the tool would commit, so a bad `/tmp` half is refused before anyone is asked, raising one `ApprovalRequired`, since an approved resume and a mode that asks nothing are refused by the commit in the same words.
The commit is one changeset over the run's roots (`_gateway`), so a batch spanning a workspace and `/tmp` lands whole or not at all.
Every approval, `apply_changes` included, carries the planner's `changes.ChangesetSummary` as its metadata: `{"creates": [{"path", "diff"}], "updates": [{"path", "diff"}], "paths": {"moves": [{"source", "destination", "is_dir", "replaces"}], "deletes": [...]}, "mkdirs": [...]}`, every path canonical and every move destination resolved to where it lands.

### The image cap belongs to the gateway, not to the reader

`multimodal.max_images` is the serving gateway's per-request image limit (vLLM's `--limit-mm-per-prompt`), set to what the operator gave that server rather than picked here.
It matters because the gateway rejects the whole request, not the call that overfilled it: an attachment set over the cap fails the turn with a message the run never sees, while the same refusal raised by the tool is a `ToolRetry` the model fixes by narrowing `pages=`.
So `ReadBinaryDocumentTool` binds its own budgets to the cap before it renders anything: `max_pages` for PDFs, where the refusal names the argument that fixes it, and `max_frames` for video and animations, which is clamped instead, since a run picks pages but never frames.
`None` is a gateway with no such limit and leaves the reader's budgets alone.
A call attaching several files divides the cap between the ones that become images before any is rendered (`run_batch`'s `shares`), since all of them reach the model in one request, while a PDF forwarded natively spends none of it.

One call obeying the cap is not enough, because what the gateway counts is the request, and three separate things fill it: a step of parallel reads each attaching one image, the turn's own attachments, and every image already in the replayed history.
No per-call budget can see any but the first, so `agents.guards.PromptImageLimit` counts the one thing that matters where the outgoing messages are in hand: it swaps all but the newest `max_images` for a note.
Trimming rather than refusing keeps the rendering the run already paid for, and the note keeps it honest — the model is told an image it asked for is not in front of it, so it reads the document again instead of answering from a picture it cannot see.
It rides on the agents in `agents/app.py` rather than being composed per chat run, because a request the gateway will reject is a hazard on every run against it, subagent and MCP ones included.
The upload pipeline's vision calls are the one exception: they run on the tool-free agent behind `llm.complete`, so converters never import the agents, and `workspace.describe` clamps the frames it samples for a caption to the cap instead.

The trim is spent on the wire and nowhere else, which is what `wrap_model_request` is for: the messages handed to the handler are what the model sees, while the graph keeps its own and records those.
`before_model_request` is not that seam — `request_context.messages` is the run's history, so a hook that edits it there rewrites the tree the conversation is replayed from and exported out of, which is how `IterationLimitWarner` came to append a nudge as a user turn nobody sent.
Nor are the messages the place to edit in place, since the parts and their content lists are shared with that tree, so the reshape rebuilds what it touches.
`ToolOutputSpill` is the deliberate exception, since it reduces the tool return itself and that is recorded in place of the original.

A chat turn's own attachments answer to the same number twice over: the trim counts them like any other image, and `_accept_attachments` refuses an over-cap set outright, served to the composer as `AttachmentLimits.max_count` so the file picker stops at it.
That gate is not what keeps the request legal any more, but a user who attached twelve images should be told at send time rather than have ten quietly go unlooked-at.

### Tables are queried, not read

`query_table` runs Polars SQL against the original CSV/TSV/Parquet/Excel file, which is the same trade `jq` makes for JSON.
A spreadsheet's markdown projection is one very long line per row, so a line read spends the context on rows the question does not need and the per-line clip silently drops the trailing columns of the rows it does return.

`file_path` takes one path or a list, and the tables are addressed positionally as `t`, `t2`, `t3`, so no path is interpolated into the SQL, a join needs nothing but the query, and the name a model types is never the exported workbook's filename, which is a sentence with spaces in it.
Every file given is registered whether or not the query names it, so `SHOW TABLES` answers from the same context the query runs in; omitting the query returns the first table's columns, types, and row count as the cheap first call.
`TableResult` splits accordingly: `tables` describes each file, while the columns and rows describe what the query made of them, which for a join belongs to no single file.

The dialect is Polars SQL and is named as such in the argument description and in the failure, because the gap that matters is discoverability rather than capability.
The motivating task — a mean and an 85th percentile over a computed load — is one query, but its ANSI spelling `PERCENTILE_CONT(...) WITHIN GROUP (ORDER BY ...)` is unsupported while Polars' own `QUANTILE_CONT(col, 0.85)` is not, and a run that reads `unsupported function` as "SQL cannot do this" leaves for Python over a spelling.
So the refusal says the dialect is Polars' and that another name may exist, and no function list is vendored anywhere: `SHOW FUNCTIONS` parses but is unimplemented upstream and `SQLContext` exposes no registry, so any list would be a hand-maintained copy going stale.

What limits a query is less the dialect than a column typed `String`, which turns a plain `SUM` or `WHERE` into a dtype error and a `TRY_CAST` retry, so schema inference runs over the whole file rather than Polars' 100-row window and every text column whose values all parse as a number or an ISO date is retyped before the query runs.
The conversion is applied only where it loses nothing, and one zero-padded value marks the column an identifier (a zip code, an EAN) and vetoes numeric typing for the whole of it.

A column that falls short is reported as a `TextColumn` with the share that parsed and a sample of the values that did not, which is what lets the _first_ query wrap it in `TRY_CAST` rather than the second.
`query_table` reports what is wrong with the data and repairs none of it, since every repair is a decision about someone else's measurements: a repeated label says a header row spilled into the data, a `<100` says a lab measured past its limit of quantification and declined to put a number on it, a `0,05` says a decimal comma.
Each wants a different answer and none is this tool's to pick, so the hint names the values and says to ask — the limit overstates, zero understates, dropping the row changes the count, and no substitution is the neutral default.
Naming the values covers every shape where a rule per shape covers one; two earlier versions reported the shapes instead (a `LabelRow` type, a `censored` regex count) and were each narrower than the values they were derived from, at the cost of a public field, a stub entry, and an extra Polars collect.

That report rides every call rather than the schema call alone, and is scoped to the columns the query named (carried in the result or spelled in the SQL, since `SELECT *` spells none) — the two halves of one fix.
A run that opens with a `SELECT` never asks for the schema afterwards, so gating the diagnosis on the query-less call left the query that most needed it as the one that never got it; and the cap that keeps the list from becoming noise itself cut by column position, so a sheet with 36 mixed columns named the first ten and dropped the only one the query was about.
The list now leads with the worst shortfall and names what the cap cut (`and 26 more`).
A column nothing parses in is text rather than a defect and stays unreported.

The schema listing is the only place a model ever sees a column name, so it spells each one the way a query has to type it: `_quoted` wraps anything that is not a bare SQL identifier in double quotes, which is the norm in an exported sheet (`"Zulaufmenge (D)"`).
A parser error answers with the same rule and one of the file's own quoted columns as the example, since that failure has nothing else to correct itself from.

An exported sheet routinely spreads its header over two rows, and the loader can only take the first, which leaves the second as data row 1 — enough to hold every numeric column in the sheet as text.
`_label_row` asks the sharp question: a column votes when row 1 holds its one unparsable value, and two columns agreeing is already past coincidence, since a genuine outlier lands in one column and not in the same row of several.
Nothing is dropped on the strength of that vote, for the same reason a censored reading is handed back rather than substituted; it is reported with the count of columns that voted, the values that gave it away, and the clause that excludes it.

Excel is read through `fastexcel` (calamine), which covers `.xlsx`, `.xlsb`, and `.xls` with no extension download, and a delimited file in a legacy encoding is retried once through `read_text_or_retry`, gated on `ComputeError` plus a delimited suffix, since a lazy scan only meets the offending bytes when it reaches them.
That retry belongs to the load phase alone, which the typing pass makes reliable by reading every text column there.
The row limit is the only cut that reaches the data, and `truncated` says only that it bound, while column and cell widths clip the rendering alone.
Every row it allowed is rendered, and a table too long to show whole is saved by the spill (see [Spilling bulk output](#spilling-bulk-output)), so both channels agree on how many rows there are.

### JSON is filtered, not read

`jq` runs a filter expression against the original `.json` file.
Omitting the filter answers with the top-level keys and their types (for an array, its length and the shape of its first element), which is `query_table`'s schema call one format over: a document's own filter cannot be written without knowing its keys, and the only other way to learn them is `.`, which returns the whole file.
The suffix table is narrower than "text that parses as JSON" on purpose, since jq is handed one document and a line-delimited `.jsonl` would fail on its second line.
The document reaches jq as the text it was read as rather than as a parsed object, so a large file is never held twice and a malformed one is reported in jq's own words as a correctable `ToolRetry`.
Every value is rendered as one compact JSON line, and a result too long to show whole is saved by the spill with every value in it.

### Registered or excluded, never deferred

A tool is in the model's initial context or it is not registered for that run, and `defer_loading` is gone from the codebase.

It had one user: `query_table`, `jq`, and `read_binary_document` each answer a question the other read tools cannot, but only for a document of one particular shape, so most turns never reach one while every turn pays for the schema.
Deferral looked like the way out, because the pointer writes the tool-search query for the model.
Naming the tool is not enough: a model whose tool list does not contain the name reads the pointer as describing a tool it was not given, and works around it rather than searching for it.
A run pointed at `query_table` for a spreadsheet instead probed for `pandas`, `openpyxl`, and `xlrd`, then hand-parsed the markdown projection — five failed calls to reach a worse answer than the first pointer offered.

The saving was never collected either.
Wire-level deferral, where the schema rides along and the provider unhides it, needs `supported_tool_deferral_modes`, which only `OpenAIResponsesModel` (gpt-5.4+) and `AnthropicModel` declare.
`llm.py` builds `OpenAIChatModel` and nothing else, so a deferred tool was struck from the `tools` array outright and `search_tools` injected in its place, a round-trip the model has to think to make on the exact turn it is already confused.

What replaces it is `settings.tools.disabled`, an operator's list of tool names in the same namespace as a chat request's `disabled_tools`, unioned with it by `build_capabilities` into the one `PrepareTools` pass a run applies.
The two differ only in who wrote them, so an operator exclusion retracts a feature's instruction block exactly as a user's does, and the pass covers `extra` as well, which is an operator's only reach over the tools a user-configured MCP server brings.
It defaults to the two conversation tools, since past conversations are already reachable through the `explore` tool's `conversations` scope.
`check_tool_settings` runs before the lifespan opens anything, because an exclusion that matches nothing withholds nothing and its only symptom is a schema the operator believed was gone.

MCP servers are added directly for the same reason (`build_mcp_server`): a user who configures one means to use it, and an operator who disagrees names its tools in the exclusion list.

`query_hint` (`tools/base.py`) keys the pointer on the entry rather than on the path the read was addressed by, since an uploaded table is only ever served as its `<stem>.md` projection and asking the requested suffix alone left `query_table` invisible for exactly the file it exists for.
`sidecar_hint` names the same tool on the refusal of the original, ahead of the extracted text it also names, and `PYTHON_INSTRUCTIONS` names it once more in the map from what a program lacks to the tool that has it.

### The MCP surface returns structured content

An MCP client gets both channels of a `ToolOutput`: the `text` a model would have read as a content block, and the structured `data` as `structured_content`, against an `output_schema` registered with the tool.

The schema is asked of FastMCP rather than assembled here (`tools/fastmcp.py`, `output_schema`).
MCP requires an object at the top, so a payload that is not one has to be wrapped, and that wrapping is a convention with three parts: the `result` property, the `x-fastmcp-wrap-result` marker `convert_result` reads back, and a `$defs` block hoisted to the document root, where the `#/$defs/...` pointers inside it resolve.
Assembling it by hand nested the definitions one level down and left every pointer dangling, so the question goes to the one place that owns the answer, and the wrap flag is read back off what comes out.

The wrapper still returns a built `ToolResult`, since a raw return value would have FastMCP replace the text and the binary attachments with a rendering of the structured content.
That is why the wrapping FastMCP does for a raw value is mirrored in `wrap_tool_output` instead of inherited, and why the payload is serialised through the spec's own adapter, which is the adapter the registered schema was derived from.

### Spilling bulk output

A tool return whose text is longer than `settings.llm.tool_output_max_chars` (60000 by default) is saved whole under the conversation's `/tmp/.tool-results/` and replaced by a preview, by one capability, `agents.guards.ToolOutputSpill`.
It rides on every agent over the user's deps (`agents/app.py`), so the chat run with its MCP servers, its subagents, and the MCP exploration are bounded by one instance, and the chat run's `ApprovalNotes` declares that it wraps it, so a note is appended to the bounded return.
The cut itself is `tools.formatting.truncate_middle`, which the FastMCP adapter applies to the text of every plain MCP tool return as well (`register_mcp_tools(..., max_chars=...)`), where there is no `/tmp` to save to and the structured content stays whole.

The decision is the harness's rather than the model's.
A tool argument that let the model write a result to a file instead of reading it was misread again and again: as an input to read from, as the way to hand a program a result it could have had by calling the tool inside the program, and as worth using on a three-entry listing, which the model then took for an empty directory.
The harness knows the size of a result before anyone has to guess it, and no tool schema, validator, or approval has to describe a file the model never asked for.

The file is `/tmp/.tool-results/<tool>-<digest>.json` holding the structured `DataChunk` data where the return carries any, since that is whole where the text may be a rendering of part of it (every grep match, every table row the row limit allowed), and `/tmp/.tool-results/<tool>-<digest>.txt` holding the text otherwise, `<digest>` being the first eight hex digits of the SHA-256 of the tool call id.
A payload that is only what the client shows, such as a subagent's transcript, is marked `ToolOutput.display_only`, so the text, which is the whole answer, is what gets saved rather than a transcript of megabytes.
The folder is the root's `reserved` one, which every tool and program reads while no ordinary change writes, moves, or deletes into or out of it, so what is evicted from it is only ever a saved result.
The data is serialized as compact JSON and sized on a worker thread, and `tmp.save_result` commits the text through the gateway like every other `/tmp` write, as the host (`Gateway.host`), which may write the reserved folder and lets the quota evict saved results.
The gateway holds the folder's lock while it derives and checks the changeset again and writes, so a spill and an ordinary write cannot both pass the cap.
Only when the cap requires it does the quota list the saved results and drop the oldest the new one would not fit beside, since a result can be asked for again while the run's own state in `/tmp` cannot, and a result that does not fit even then is not saved at all.
An ordinary change never evicts one.

The preview is the head and the tail of the text, three parts to one and cut at line ends, around a note naming the file, its size, its entry count when the data is a list, whether the tool had already cut the result short, and how to read on: `jq`, `grep`, or `run_python` on a `.json`, and `read_document` from the first offset the head left out on a `.txt`.
The tail stays because that is where a tool's own hints land (`hint_suffix`), and the cut is read off any payload whose `truncated` is set, so a capped query saved to a file does not read like the whole of it.
Only the return value is replaced, so the `DataChunk` the frontend renders rides through untouched.
A content list such as `[text, BinaryContent]` has its leading text bounded the same way and keeps its other items as they are, so no image is stringified.
A run without a `/tmp`, such as the MCP exploration, and a result the folder cannot hold get the same head and tail around a plain count of what was left out.

With every return bounded by the adapters' one cut, the tools lost their own display budgets (`max_formatted_chars` on `grep`, `jq`, `query_table`, `search`, and `web_fetch`), which had cut the text while the data stayed whole and left the rest out of reach, and `run_python` no longer clips what a program printed or returned, so its data keeps all of it.
What bounds a call's data is still the tool's own: `row_limit`, `max_results`, `max_chars`, `max_line_chars`, and the column and cell widths.
`read_document` keeps a single content budget, `max_chars`, which the agent derives as two thirds of `tool_output_max_chars`, so that a window rendered with its line numbers stays under the spill threshold, since a read resumes from an offset into the document rather than from a saved copy of the window.

The note in a preview says how to read on, so no prompt block repeats it, and saving a result as a workspace document is the `run_python` guidance's to state.

The listings name what they left out on every result, not just an empty one, through one `omission_hints` in `tools/formatting.py`: entries `include_ignored` would reveal, entries below `max_depth`, and the `max_results` cap.
Each count is what changing that one argument would reveal, and grep's hidden count is a floor, since ripgrep never enters the build and vendor directories it was told to skip.

## The Python sandbox

`run_python` is a Monty sandbox with no network or host filesystem access and a budget on execution time and memory that surfaces to the model as a plain `TimeoutError` or `MemoryError`.
A failed program is a `ModelRetry` and repairing it is how a run converges on Monty's subset, so the agents carry no separate retry budget: `llm.retries` defaults to `None`, which hands `request_limit` down as the budget (`LlmSettings.retry_budget`), leaving the turn's own bound the one thing that ends a correction loop.
A program comes from either inline `code` or one scoped workspace `.py` `script_path`, which is reloaded on every call so the model can repair it with the edit tool and rerun it, which is why `PYTHON_INSTRUCTIONS` sends anything past a few lines to a `/tmp` script and keeps inline `code` for a throwaway.

### The mount

The workspace is mounted copy-on-write and a program opens a document by the path everything else in the turn uses (`~/reports/q1.md`), so the string a tool result returns, a citation carries, and a program opens is one string, and it may open a path it only discovers while running.
The conversation's folder is mounted at `/tmp`, the spelling every tool uses for it too, which is the whole grammar a program has to hold: no mount prefix to add on the way in and none to strip on the way out, and `path_iterdir` hands back the same spelling it takes.
Any other absolute path is refused with the roots named rather than kept in memory for the call, since whatever a program wrote there would be gone when the next call looked for it, which is exactly the trap a per-run in-memory `/tmp` used to set.
The run's working directory is `WORKSPACE_MOUNT`, so Monty resolves `~/reports/q1.md` to `/workspace/~/reports/q1.md` before the mount sees it, which also makes the absolute form a model that has met another sandbox reaches for name the same document, while listings and refusals never produce it.
It stops at the mount: a tool argument is refused for it like any other path that leads with no root, since the approval gate resolves the declared path before the tool sees it and a spelling the tool rewrote afterwards would be gated and shown to the user as a different document than the one written.
A path that leads with neither (`/workspace/notes.md`, the scope dropped in between) is refused with the roots named, the sandbox's own spelling of `workspace_root_hint`.

`tools/workspace_os.py` is that mount: an `AbstractOS` whose every operation routes through `resolve_accessible_file` and `entry_visible`, the seams the read tools use, so the `DocumentFilter` stays one predicate instead of gaining a third enforcement surface, a hidden document reads as absent rather than as refused, and a legacy encoding is decoded rather than served as mojibake.
`pydantic_monty.MountDir` would have been less code and none of that, since it maps a host directory in whole.
Whether the mount answers an operation, refuses its path, or hands it to `inner` (the environment and the clocks) is decided once, in the `dispatch` override `AbstractOS` offers for it, rather than by a prologue at the head of every method: asking per method made forgetting one a silent bug, which is what an unimplemented `path_open` once was.

Nothing is handed to a program as a host function that the mount already covers: `open` and `iterdir` are the read tools, `re` is grep, and `json` is jq, and ranking a chunk against a question is a `search` call the model makes before it writes the program.
Monty's `json` has `loads` and `dumps` and no file-reading `load`, which `PYTHON_INSTRUCTIONS` says outright, since a program is how a saved `/tmp/.tool-results/*.json` is read whole.
Monty has no `glob`, `rglob`, or `fnmatch`, which is why `PYTHON_INSTRUCTIONS` shows the `iterdir` walk, and `path_iterdir` returns its entries sorted, since Monty cannot compare two `Path` values.
Which modules a program may import is left for the model to find out, since Monty implements a subset only it knows and offers no `importlib`, `sys.modules`, `__import__`, or `dir` to enumerate one: any list written down here goes stale on the next release, and the one that used to stand in `PYTHON_INSTRUCTIONS` advertised `functools` before Monty had it, for as long as nobody tried it.
A failed import raises `ModuleNotFoundError` naming the module, which is the correction a stale list would have needed anyway.
A program parks intermediates and the state a later call needs in `/tmp`, named by `TMPDIR`, because Monty has no `tempfile` and anything under the working directory is staged for the user's approval.
The environment mirrors a shell's: `PWD` is `/workspace` like `os.getcwd()`, `TMPDIR` is `/tmp` where the run has a conversation, `LANG` and `LC_ALL` are `C.UTF-8`, and `agents/tools/python.py` adds `HOME` (`/workspace/~`, what `~/...` resolves to) and `USER`/`LOGNAME` (the user id).
Monty has no `os.path`, `Path.home`, or `expanduser`, so `HOME` is the only absolute spelling of the personal workspace a program meets, and no `PATH` is set, since there is nothing to execute.
Bytes are refused on the mount in either direction, so a document with no text form stays `read_binary_document`'s.

### Writes from a program

A program changes the workspace with the ordinary `Path` and `open` calls: write, append, `mkdir`, `unlink`, `rmdir`, and `rename`, for documents and directories alike.
None of them reaches the disk while it runs, since a filesystem callback can neither await the changeset gateway nor stop for an approval.
`WorkspaceOS` records each one in a copy-on-write overlay (`nodes`: canonical path to `Text`, `Ref`, `Deleted`, or `Dir`) that every later read, `stat`, and `iterdir` consults before the disk, so a program reads its own writes.
A rename records a `Ref` to the file it carries and copies no bytes, so a PDF moves as freely as a note, and a directory rename re-keys what lies below it.
The first touch of a disk file records its `(mtime, size, inode)` stat as the basis its change commits against: a stat rather than a content hash, since a program walking the workspace reads far more than it changes and hashing every read would cost what the per-document budget saves.

Limits are checked as each change is recorded and raised inside the program, so the model can correct it: the writable span (`agents.tools.write.program_paths`, the one `write_document` uses, only `/tmp` in read mode or where `apply_changes` is withheld, which therefore refuses every change to the workspace), `.assets` payloads (hidden from the mount entirely, since they belong to their entry), `writes_as_text` for a new file, the per-document cap, and `ChangesetLimits` from `settings.sandbox.max_changeset_*` (required on the tool side, so the defaults live in the settings alone) for the operations staged, one per path changed with an explicit new directory counting and the parents created along the way not, the deletions among them, and the characters written in total.
A document keeps its extension when renamed, and a rename onto a file replaces it as on POSIX while a directory neither replaces nor is replaced.
A directory is removed only when nothing is left in it on disk, since one that looks empty to the program may still hold a filtered or excluded file that staging its delete would take along.
On a case-insensitive workspace a path is respelled the way the overlay or the disk already spells it, through the one seam every tool respells in (`canonical_local_path`), handed a listing that puts the overlay's live names before the disk's, so one file keeps one `path_key` identity, and a filter is asked about the name the file has.
The disk listings and what `base` found at each path are read once per run, which also lets staging resolve the entries of one folder without listing it once per file.

`/tmp` is recorded in the same overlay, so a program reads its own state back while it runs and one that fails leaves nothing behind.
The overlay is partitioned by its roots' policies: a direct root's nodes are not charged against `ChangesetLimits`, which exist to keep an approval readable, while the character cap still bounds what the overlay holds.
A rename never carries anything across policies, since no one commit can move a file between a folder and a workspace: one between them is a write of the file's text and a removal of the source, each landing the way its own root commits, and a directory takes part in none.
A rename within `/tmp` records a `Ref` like one within a workspace, so a directory or a binary moves as it is, and a file there may change its extension, since it is no entry.
So leaving `/tmp` is a workspace write the user approves, and moving a workspace document into `/tmp` writes the copy at once and stages the removal the user approves, while a binary, which has no text to copy, is refused.

Once the program succeeds, `tools.changeset.stage_changes` spells the overlay as one `changes.Changeset[str]`, a direct root's nodes as plain moves, deletes, new directories, and writes, a rename onto a file deleting it, and a gated root's in the gateway's terms, which needs no ordering, since the overlay already maps final paths to the disk paths they come from: a carried directory is one `Move`, a renamed part of an entry is one `Move` of the whole entry (its companion and `.assets` going along), a file written after a rename is that `Move` and a `Write` at the destination, a rename onto a file is a `Delete` of it and the `Move`, a change inside a moved directory names the file as it is now, a removed directory is one `Delete` covering what was below it, and an empty new directory is one `CreateDir`.
Removing an entry's original removes the entry.
Removing only the description while the original stays is refused, since the description is the original's searchable text, as is an entry whose parts move to different names or move while another part goes, and a delimited file is checked against its header like any whole write.
What the user approves is the planner's summary, the same one every other mutation shows, built from what the gateway resolved rather than from the overlay.

`agents/tools/write.py` (`changeset_committer`) then routes every item to its root, over the roots the program may write.
In write mode every item is applied at once, as one changeset, by the same rule the gate applies to every other workspace write, and `PythonResult.changeset` is an `AppliedChanges` with the gateway's reports of the workspace items, picked by their root.
Otherwise the whole changeset is planned first, so a changeset the gateway would refuse fails the run before anything is written, then the `/tmp` items are applied and the workspace items stored by `staging.stage` as one JSON file under `<data_dir>/changesets/<owner>/`, which survives a reload or a restart, and `PythonResult.changeset` is a `PendingChanges` with the id and the planner's summary.
A changeset whose `apply_changes` call the next request denies or abandons is discarded there (`discard_unapproved_changes`), and the background prune drops what outlived `staged_changeset_ttl_hours` unanswered.
Files rather than a table, since a staged changeset is write-once, read-once state with no query beyond its id.
What is stored is the program's own spelling, a `Changeset[str]` of canonical paths with bases, under its owner's directory, so ownership is the path and discarding one is an unlink: the paths are routed again with the approver's stores when applied, so access revoked in between is honoured.

`apply_changes(changeset_id)` is the second half, registered with the write tools.
Its validator plans the stored changeset again and raises one `ApprovalRequired` with the summary, which the frontend renders as the full list of changes, and an approved resume neither loads nor plans it again.
Applying it runs `apply_changeset` like every other mutation tool and returns the gateway's reports, and a basis that moved since the program ran is a 409 the model is told to answer by running the program again.
The changeset is discarded either way.
Every mutation surface, the dedicated tools and the sandbox alike, now commits through that one gateway.

### Injected tools

The mount is why a program needs almost no tools.
What it cannot be is the four whose answer lives somewhere the sandbox cannot reach, so those are injected as host functions by `tools/monty.py`: `search` needs the database, `web_search` and `web_fetch` need the network, and `query_table` decodes a spreadsheet the mount refuses as binary.
Before them, a program that needed a spreadsheet's rows could only read a file a separate tool call had written first.

Nothing that mutates is injected and nothing can be: a running program cannot stop to ask for approval, which is the same constraint that makes it stage its changes for `apply_changes`.
Every injected function is a read, so the mode gates none of them.
What does gate them is whatever gates the tool of the same name, since the two are one namespace: `web_enabled` folds the operator's master switch and the host policy into one flag for the web pair, and `sandbox_surface` unions `settings.tools.disabled` with `deps.disabled_tools` itself.
That union is computed there rather than carried on deps, so a `UserDeps` built without it — the debug console's, the MCP one's — still cannot hand a program a tool the operator disabled.
A tool hidden from the model's tool list must not come back as a function it can call from a program.

The set is not a list anyone maintains.
`agents/tools/python.py` keeps one list of sandbox functions, filtered from the very tuples that register the tools (`EXPLORE_FACTORIES`, `WEB_FACTORIES`) plus the one function no tool list carries, and each tool class declares how it is offered: `Tool.injectable` (default `False`) whether a program may be handed it, `Tool.registered` (default `True`) whether the model is handed it too, and `Tool.sandbox_instructions` the guidance the prompt carries while it is live.
The names `disabled` may withhold (`SANDBOX_FUNCTION_NAMES`), the names `sandbox_only` may move (`INJECTABLE_TOOL_NAMES`, the registered ones), and the guidance are all derived from that list.
So a renamed factory moves the tool list and the sandbox together, the web pair drops out because `WEB_FACTORIES` is already empty when the operator's switch is, and nothing has to check that an injectable name is registered.
Adding a fifth is one line on its class, and the question it answers is whether the mount could have done the job, which is why `grep` and `read_document` are not on it.

One function is handed to programs alone: `complete(*, prompt: str) -> str`, one tool-free model call for classifying, extracting from, or summarizing many items in a loop, modelled on a sub-agent call in `pydantic-ai-harness`'s dynamic workflows.
It is `tools/complete.py`'s `CompleteTool`, declared and type-checked like the others, injectable and not registered, so `sandbox_only` cannot name it while `disabled` withholds it like any tool.
The tool is handed only the completion, so `tools/` stays free of models and runs, and `agents/tools/python.py` supplies it, which is why `run_python` is registered with `takes_ctx=True`, pydantic-ai's own name for a factory that gets the `RunContext`, not only the deps.
The program never picks the model: it is the aux tier the request resolves (`UserDeps.aux_llm`), the operator's model for many small one-shot calls, falling back to the main one, with thinking off and `settings.sandbox.completion_max_tokens` as each call's output cap, built on a program's first call and reused by the rest.
Each call runs `llm.complete` on the run's `usage` and `conversation_id`, under its `usage_limits`, so the calls count toward the turn and against its request limit.
`UserDeps.completions` (`agents.common.Completions`) is the turn's one object for them, held on the deps so every program and subagent of the turn shares it.
Its `slot` counts a call against `settings.sandbox.completion_max_calls` before the call's first await, so an `asyncio.gather` cannot pass it, and then waits for one of the `completion_concurrency` calls in flight.
It is also the capability of each call's run, whose `wrap_model_request` holds a request slot through every request, retries included, since pydantic-ai checks the request limit before a request and counts it after, and refuses a request that would leave the parent none for the response carrying the program's result.
Both refusals reach the program as a `ValueError` it may catch, while a spent turn budget still ends the turn.
A subagent's own requests hold no slot, so a subagent that meets the request limit ends the turn as before, where a program's completion is only refused.
`completion_timeout_seconds` bounds each call, since Monty's own clock leaves host time out.

A program is handed the structured `data` channel as the adapter for the tool's declared result type renders it: the same type the rendered stub names and `return_schema` publishes, so what a program receives and what it was told to expect are one description read twice.
So a program sees plain dicts and lists, reads a field as `hit['filename']`, and gets the whole result: the budgets, truncation, and hints on the `text` channel exist to fit a context window a program does not have, and a program that wants fewer rows says so in its query.

### Declarations and the type check

The declarations `monty_surface` builds have two consumers: the model reads them as the API it may call (`SANDBOX_API_INSTRUCTIONS`, composed per run so it never names a function the gate withheld), and the sandbox takes them as `type_check_stubs`.
The rendering is pydantic-ai's `FunctionSignature`, which is what `pydantic-ai-harness`'s code mode uses for the same job, so this module supplies two JSON schemas per tool and does no type rendering of its own.
A hand-rolled renderer that stood here first marked every field required, dropped each field's own description, and — keyed on the bare class name — declared two tools whose result records share a name with the _first_ one's fields, so the program that read its own result correctly was the one the type check turned away.
`FunctionSignature` states a defaulted field as `NotRequired`, carries the descriptions through, and prefixes a conflicting record with its function's name (`search_Result`, `query_table_Result`).
Arguments are keyword-only, and the return schema is taken with `json_schema(mode="serialization")`, which is what makes a `tuple` field a list and a model a plain dict: the stub describes what actually arrives inside the program.

What the model reads (`MontySurface.declarations`) and what the checker gets (`MontySurface.stubs`) differ by one line, which makes `open` resolve.
`open` is host-provided rather than a Monty builtin and sits in no module the checker can reach, so without that line the checker rejects every program that reads a document.
It declares no types, deliberately: Monty's own `pathlib` stub types `Path.open()` as `Unknown`, so there is no file type to reuse, and a spelled-out handle would be one this repo invented against a runtime it does not own — the first attempt refused an `encoding=` keyword, a third positional argument, and `seek`, each of which the interpreter accepts.
A stub that rejects a working program is worse than one that checks nothing, and the checking that earns its keep is on the injected declarations, which are generated from real signatures and cannot drift.

`settings.sandbox.type_check` is on by default: a misread field and a forgotten `await` are both caught before the program runs, with a diagnostic that names the field.
It is a whole type checker rather than a check of the stub, so it also rejects unsound code that would have run, such as `Path(os.getenv("TMPDIR"))` for the `str | None` it is.
`SANDBOX_TYPE_CHECK_INSTRUCTIONS` is added to the prompt only where the check is on, so a run never promises a correction it will not make.

### Where a tool lives

`settings.tools.sandbox_only` is a third answer beside the model having a tool and not having it: the tool is injected as a function and dropped from the tool list, limited to `agents.tools.INJECTABLE_TOOL_NAMES` and overridden by `disabled`, so a tool disabled outright reaches neither surface.
This is what `pydantic-ai-harness`'s code mode does for every sandboxed tool, and for the same reason: a schema and a declaration of the same tool, both sent on every request, is one of them wasted.
It costs the model the ability to make the call on its own, so it suits a tool whose schema dwarfs its declaration and whose result is nearly always an input to further work rather than an answer.
The catalog lives in the agent instructions rather than in `run_python`'s description, so the tool-definitions block stays byte-stable across turns and the prompt prefix keeps caching.

Two things rule a tool out even where that arithmetic favours moving it.
The first is a consumer of its tool part: `components/chat/tools/search.ts` and its neighbours read the `data-tool-output` part and call `addChunk` with the line range and character offsets the document view highlights from, and a call made inside a program emits no tool part, so those citations would stop.
The second is a pointer: `query_hint` and `sidecar_hint` name a tool on a refusal, and a model that follows a pointer to a name carrying no schema is one indirection short of the call, which is the position `defer_loading` was removed for leaving it in.

Both tests have been run against a real candidate.
`query_table` clears the arithmetic by a wide margin (~1.9k characters of schema against ~570 for its declaration) and is the one injectable tool no citation handler reads, so it was moved; it failed the second test, because `read_document` points a read of a table at it, and it was moved back.
A tool that clears all three is registered and injected both: the turn that only wants the answer makes the call, and the program that has already loaded the file does not spend a turn to query it.
What makes this different from the removed `defer_loading` is that the name never disappears: a `sandbox_only` tool is declared in full, with its signature and result shape, in the sandbox API block.

There is one published set, `unlisted_tool_names`, which is what the schema pass and the settings listing both drop.
What the sandbox withholds is a narrower question — the operator's `disabled` plus the request's, without the sandbox-only names — and it is answered inside `sandbox_surface`, where it is used, rather than exported as a near-twin of that name.

### Budgets

The read budget is per document (`max_document_chars`) and deliberately not a running total, since a decoded document is the only thing the host holds and it holds one at a time: `read_text_file` reads a file whole and decodes it in one go, then the string crosses into the interpreter and is dropped, so reading a 2000-document, 100 MB workspace moved the server's peak RSS by a megabyte and took half a second.
A running total would have bounded nothing the host spends while capping the very thing the mount exists for, and it did: the 5M default refused a walk of that workspace a third of the way through.
The per-document cap is enforced twice, since a byte count only bounds a character count from above: `check_read_budget` refuses a file by size before it is decoded, then the exact length is checked once the text is in hand.
Inline `code`, the stored script, and every file a program writes answer to the same cap, each being one text the host holds whole.

What a program retains is `max_memory`, the interpreter's own budget, and how long it spends retaining it is `request_timeout_seconds` and the agent's `tool_timeout_seconds`, since `max_feed_duration_secs` counts bytecode alone and not the time a host callback takes (measured: five 0.4 s host reads complete under a 0.5 s limit).
Sleeping is off that clock too, so `max_total_sleep_secs` caps it with the same budget, which a program with a rate-limited web tool to pace still has room for.
The cumulative caps are the `max_changeset_*` settings, because what a program writes is held in the overlay until the run ends and then lands on disk, so a loop writing the same megabyte a thousand times would otherwise spend a gigabyte nothing else here bounds.
The worker pool is owned by the FastAPI lifespan (`sandbox.py`), like the HTTP clients, because a tool instance is built per call and a pool per call would spawn and reap a worker every time.
Every call takes a fresh session out of it, so no program sees another's variables and a session a time limit stopped mid-operation, whose heap Monty no longer vouches for, is never fed again.

`HostCalls` applies one policy at the boundary to every function a program is handed.
`settings.sandbox.max_host_calls` (100) caps the calls one program makes, `complete` included, through a `CallBudget` taken before the call's first await, so an `asyncio.gather` cannot pass it.
A spent budget raises a `RuntimeError` inside the program, a `ToolRetry` or an invalid argument a `ValueError` it may correct, and any other failure a `RuntimeError` named by type alone, since a host error may carry host details.
`UsageLimitExceeded` (`tools.monty.TURN_ENDING_ERRORS`) is not a program failure, since the turn's shared budget is spent for every run in it: `HostCalls` keeps it and refuses every later call, and `run_python` raises it once the program stopped, however the program handled it, so the turn ends instead of reporting a retry.
Every call that returned or raised is kept as a `HostCall`, a bounded preview of its arguments and its result or error, in `PythonResult.calls`, which the `run_python` card lists.
A failed program's retry lists up to 20 of them before the traceback, so the next attempt builds on what came back instead of paying for the same calls again.

## Video and animated media

Vision chat models only accept still images: the OpenAI Chat Completions spec has no video content type, and pydantic-ai's OpenAI path rejects every form of video content with `NotImplementedError`.
All animated media is therefore represented as a bounded set of still frames sampled evenly across the timeline, each downscaled and labeled with its timestamp — the same shape Qwen's own video preprocessing produces internally.

```
upload .mp4/.webm/.mov/.mkv          upload animated .gif/.webp
        │                                     │
 workspace.prepare._prepare_video      workspace.prepare._prepare_image
 (entry_kind = video)                  (entry_kind = image)
        │ ffmpeg/ffprobe                      │ Pillow ImageSequence
 converters.video.sample_video         converters.video.sample_animated_image
        └──────────────┬──────────────────────┘
                       ▼
        MediaSample (≤ 8 PNG frames + timestamps + duration)
                       │
        asset_processing.caption_frames (aux vision model)
                       │
        <stem>.md description → chunks + embeddings (pgvector)
```

The same sampling backs `read_binary_document`: for videos and multi-frame GIF/WebP it attaches the sampled frames (each tagged `<path>#t=<ts>s`) instead of the raw container bytes, which would otherwise reach the model as its first frame only, or blow the serving gateway's request size limit.
Static images keep the verbatim pass-through, and single-image captioning (`caption_image`) is untouched.
Frame extraction for container formats shells out to `ffmpeg`/`ffprobe` via `subprocesses/ffmpeg.py`; GIF/WebP animations decode in-process with Pillow.
Reconversion needs no special casing, because `workspace.reconvert` re-dispatches the stored original through `_phased_upload`.

The serving stack is converging on native video: Qwen3.6 checkpoints understand video directly and llama.cpp merged ffmpeg-based video input in June 2026, but pydantic-ai cannot send video through the OpenAI protocol, so the frame sampling stays.
When the full chain supports it, the swap is local and the rest of the entry plumbing (entry kinds, description projection, chunking) is format-agnostic:

- `ReadBinaryDocumentTool._read_video` / `_read_animation` attach the media as one `BinaryAttachment(media_type="video/…")` instead of sampled frames.
- `caption_frames` sends the video as a single `BinaryContent` instead of interleaved frame images (watch the aux model's context budget — native video tokens are far heavier than 8 frames).
- `converters/video.py` and `subprocesses/ffmpeg.py` shrink to media-type tables or disappear.
