# RAG System

- Integration through Vercel AI Data Stream Protocol implemented by Vercel AI Elements in frontend and Pydantic AI in backend
- Development environment with Nix Flakes
- Project has not yet been deployed, don't care about breaking changes and migrations, prefer clean solutions
- Model structured data with a fixed set of keys as a frozen dataclass, Pydantic model, or `TypedDict` (TypeScript: an `interface` or `type`), never a bare `dict[str, Any]`.

## Frontend

- SPA React app with Typescript
- Vercel AI SDK UI for user interaction
- shadcn/ui and Vercel AI Elements for styling and components

## Backend

- FastAPI server for handling requests and responses
- Pydantic AI for LLMs and agents
- PostgreSQL with `pgvector` is the only supported database, reached in dev/test over the `services-flake` Unix socket under `data/db/`.
- Chunk metadata, text, and vectors live in the `chunks` table and cascade with their document, so there is no separate vector index to reconcile.
- Alembic manages the schema (see `backend/README.md`): whenever you touch `db/models.py`, hand-write a matching revision under `migrations/versions/` in the same change, since autogeneration needs a live database.
- Row creation must be atomic, never `s.get`/`_find` then `s.add`: use `INSERT ... ON CONFLICT` (`ensure_row` in `db/_common.py`) or a transaction-scoped advisory lock for multi-row sequences.
- The tools under `tools/` must not read `hivegent.config.settings`: every tunable is a dataclass field with a default, applied where the tool is instantiated (`agents/tools/*`, `mcp/tools/*`).
  Importing sibling infrastructure (`converters`, `chunkers`, `subprocesses`, `security`) is fine.

### Workspace

- `data/workspace/` is the source of truth for document content, and the `documents` and `chunks` rows are an index derived from it.
- Mutations should go through the API.
  Hand-dropped markdown and plain text is ingested on startup (a plain-text original gets its `<stem>.md`), files needing a converter stay inert on disk until uploaded or reconverted, and reconciliation never deletes workspace files.
- There is no working directory: a document path is always full and prefixed, missing subdirectories are created, and a path naming no known root is refused with the roots named (`tools.base.workspace_root_hint`).
  The grammar is stated once per surface, `WORKSPACE_PATH_INSTRUCTIONS` for an agent run and the FastMCP `instructions` for MCP, so no argument description repeats it.
  It binds what comes back too: a message naming a path renders it through `workspace.paths._shown` (`store.scope.render` outside the package), since a store-local spelling names a path no tool and no route accepts, and the `workspace` mutations take the store rather than its directory wherever that is what it costs to say which workspace a path is in.
- The mutating tools span the personal workspace plus the groups the user may write to, the read tools every readable one, and `store.scoped_operation` routes each path back to the workspace its `~` / `@<group>` prefix names.
- A group is identified by the ID from its OIDC groups claim and nothing else (the `groups` row, `documents.owner_group_id`, the `group:<id>` directory, the `@<id>` prefix).
  `auth.parse_group_claims` is the one place that knows how a provider spells that claim, and a display name is a client label only (`GroupInfo`, `User.group_labels`).
- Never read user content with `Path.read_text`: use `text.read_text_file` / `text.decode_bytes`, which handle Windows encodings, return `None` for binary, and report the source encoding.
  Writes are always UTF-8.
- Path strings entering the system are folded to NFC by `config.normalize_unicode` (`sanitize_document_path` for HTTP, `tools.base.resolve_search_path` / `scope_paths` / `_glob_entries` for tool arguments), since macOS hands out decomposed filenames.
  File content is never normalized, since `content_digest` fingerprints it verbatim.
  Out-of-band files keep their on-disk spelling in `stem_path` until `workspace.normalize_workspace_paths` repairs disk and SQL in one idempotent pass, which `reconcile_store` runs before the ingest.

### Scratch

- A `.scratch/` directory is content and never a document: `entries.is_scratch_path` is checked before the format seam, so the reconcile walk skips it, a write lands as plain bytes with no projection or stem claim, and the tree hides it.
  That is where a run parks state between `run_python` calls.
- The path tools still list, glob, and grep it, since a run has to find its own state back, while `_check_not_reserved_path` keeps the upload, move, and directory API out of it as it does `.assets`.
- The approval gate is per path, not per tool (`agents/tools/write.py`): an interactive run writes to scratch without asking, read and plan modes refuse it like any write, write mode approves everything.
- It is cleared by `workspace.cleanup_scratch_dirs` from the lifespan and, while running, by `DELETE /api/scratch` ("Clear Scratch"), which sweeps the caller's workspace plus every writable group under its lock and notifies no client.
- `SCRATCH_INSTRUCTIONS` is shared between the `compute` and `write` features, and `PYTHON_INSTRUCTIONS` names `.scratch/` as the home of a rerunnable `.py`, since a `.py` elsewhere is an original and gets chunked.
  The mutation receipt says the rest at the one moment the path is in hand: it spells the prefixed path a tool takes back and points a `.scratch/` `.py` at `run_python`'s `script_path`.
  That pointer is a `MutationHint` the write and edit tools take like `filter_func`, injected by `agents/tools/write.py` alone, since the MCP surface writes through the same tools and has no `run_python`, and applied where the tool holds both spellings: `local` answers `is_scratch_path`, `target` is what the model types back.
  `run_python`'s own `output_sink` composes the writer without it, since a commit the model asked for by declaring an `output_path` is not a program it just stored.

### Notifications

- Every workspace change reaches the client on the per-owner SSE feed, which the frontend turns into a refresh of the named scope.
- Long-running work is a real job and its settled `document.*` event carries it.
  A mutation that ran inline must publish `ScopeChanged` via `workspace_events` (`notify_workspace_change` holding the store, `announcing_mutator` for the write tools), or every client but the one that asked stays stale.
- A scratch write is the exception, since the tree hides it and the refresh would buy nothing.
- The notification skips the `X-Client-Id` that caused it, so the asking tab keeps its own read-after-write and the others learn from the feed.
- Delivery is per-owner, so a group workspace refreshes for the writer, not yet for the other members.
- `ScopeChanged` is transient and never retained, so the client re-reads every scope it holds on each handshake (`onFeedReady`).

### Conversations

- Chat history is a server-authoritative message tree in SQL and the browser is never its source: compaction is one more turn of the persisted conversation, and tool approvals, the export archive, citations, and attachments all follow from that.
  See `backend/README.md`.
- Never relax `extra='forbid'` on the Vercel AI request models to accommodate a client, since it is what stops a malformed `approval` from re-matching the unanswered variant and releasing a gated call.
  A part the AI SDK builds but pydantic-ai has not transcribed is fixed upstream (the floor is the lockfile, pydantic-ai >= 2.34), and `tests/unit/test_client_messages.py` pins the shape a real client posts.
- UI-owned state (approval decisions, reasoning durations, the turn error) rides on request metadata and is never sent to the provider, never on `ToolReturnPart.metadata`, which is the tool-output channel.

### Tools

- The chat's document selection is asymmetric: `included_documents` is advisory and only named in the prompt (`parse_document_scope`, `format_document_scope`), never a filter, so a run can follow a reference out of the selection.
  Only `excluded_documents` becomes a `DocumentFilter`, enforced by the path tools through `SearchPath.filter_func` and by retrieval in `resolve_accessible_document_ids`, staying one predicate.
  `.scratch/` is exempt inside `DocumentFilter.__call__`, which lets `UserDeps` offer a single `search_paths`.
- A document is writable exactly when it is readable as text: the write tools take markdown descriptions and plain-text originals (regenerating the projection through the upload pipeline) and reject binaries, which are replaced by uploading.
- Tables are queried (`query_table`, Polars SQL) and JSON filtered (`jq`) against the original file, never read, and `converters.TABULAR_SUFFIXES` / `converters.JSON_SUFFIXES` are the one table behind each split, which `read_document` points at.
  The pointer follows the entry, not the path in hand (`tools.base.query_hint`): an uploaded table is served as its `<stem>.md` projection, so the read that needs the hint most never names a tabular suffix, and `sidecar_hint` names the same tool ahead of the extracted text when the original itself is refused.
  The read tools still serve the markdown projection, which retrieval and citation anchors are built on.
- Every bulk-output tool declares an `output_path` that stores the result in the workspace and returns a receipt, and that write answers to the same approval gate as the write tools.
- A tool is in the model's initial context or it is not registered: nothing is deferred, MCP servers included.
  `defer_loading` hid a tool from the tool list and offered it back through tool search, which bought nothing here (`llm.py` builds only `OpenAIChatModel`, which renders no wire-level deferral) and cost a model that read a pointer at a hidden tool as naming one it was not given.
- `settings.tools.excluded` is what replaces it: an operator's tool names, in the same namespace as a request's `disabled_tools` and unioned with it into the one `PrepareTools` pass, which reaches MCP tools too.
  It defaults to `jq` and the two conversation tools, and `agents.check_excluded_tools` fails the boot on a name no tool has.
- See `backend/README.md` for the typing pass, the budgets, the redirect channels, and why nothing is deferred.

### Python sandbox

- `run_python` is a Monty sandbox with no network or host filesystem: the workspace is a read-only mount (`tools/workspace_os.py`, routing through the same seams as the read tools so the `DocumentFilter` stays one predicate), `.scratch/` is the only writable path, and a document is written only by committing `/output` through the canonical write path after the program succeeds.
  The declared `output_path` is a second name for `/output` inside the program (`WorkspaceOS.output`, seeded with the document as it stands), so the two names are one file and not a conflict, while a write to any other document stays refused.
  Only `/output` is ever named to the model, though, since `output_path` captures the call's result on every other tool (`REDIRECT_INSTRUCTIONS`) and a model carrying that meaning over declares a path, returns the document it computed, and writes nothing: the arg description, `PYTHON_INSTRUCTIONS`, the mount's refusal, and the sentence for a run that committed nothing all say the program writes `/output` itself.
  There is one path grammar and the sandbox does not add to it: a program opens `~/notes.md`, the path every tool result, citation, and argument spells, while a leading slash is the run's own files (`/tmp`, `/output`).
  `WORKSPACE_MOUNT` (`/workspace/~/notes.md`) is recognised inside a program but never produced, since a model brings the convention from elsewhere; a tool argument keeps the one grammar, so the approval gate and the commit never read a path differently.
  Nothing else is injected as a host function, since `open` is the read tools, `re` is grep, and `json` is jq.
  See `backend/README.md` for the mount, the budgets, and what a program may import.

## Testing

- Every test must be stateless: only a temporary filesystem (`tmp_path`, the `data_dir`/`user_store` fixtures) and module state guarded by `monkeypatch`.
- No test may reach a live database or other stateful service.
  Stub the paths that would hit PostgreSQL with `monkeypatch` (e.g. `chunk_and_index_document` to a no-op).
- The live-DB surface (migrations, the running server, retrieval) is covered by manual smoke tests and the dev stack.
- Run `uv run pytest` for the backend and `npm test` for the frontend, both without external services.
