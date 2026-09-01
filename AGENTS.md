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
- The path tools still list, glob, and grep it, since a run has to find its own state back, and `delete_document` removes one file from it, while `_check_not_reserved_path` keeps the upload, move, and directory API out of it as it does `.assets`.
- The approval gate is per path, not per tool (`agents/tools/write.py`): an interactive run writes to scratch without asking, read and plan modes refuse it like any write, write mode approves everything.
- It is cleared by `workspace.cleanup_scratch_dirs` from the lifespan and, while running, by `DELETE /api/scratch` ("Clear Scratch"), which sweeps the caller's workspace plus every writable group under its lock and notifies no client.
- `SCRATCH_INSTRUCTIONS` is shared between the `compute` and `write` features, and `PYTHON_INSTRUCTIONS` names `.scratch/` as the home of a rerunnable `.py`, since a `.py` elsewhere is an original and gets chunked.
  The mutation receipt says the rest at the one moment the path is in hand: it spells the prefixed path a tool takes back and points a `.scratch/` `.py` at `run_python`'s `script_path`.
  That pointer is a `MutationHint` the write and edit tools take like `filter_func`, injected by `agents/tools/write.py` alone, since the MCP surface writes through the same tools and has no `run_python`, and applied where the tool holds both spellings: `local` answers `is_scratch_path`, `target` is what the model types back.
  `run_python`'s own `output_sink` composes the writer without it, since a commit the model asked for by declaring a `commit_path` is not a program it just stored.

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
- A document is writable exactly when it is readable as text: the write tools take markdown descriptions and text originals of any format (regenerating the projection through the upload pipeline) and reject binaries, which are replaced by uploading.
  Creation asks the same question from the name alone, since a file that does not exist has no bytes to decode: `converters.writes_as_text` refuses a suffix on `converters.BINARY_SUFFIXES` and admits every other, so `.csv`, `.html`, and `.svg` are created as the text they are.
  Not `is_projectable_original`, which answers whether the ingest may derive a projection by copying and so refused every text format a converter claims while admitting binary ones no converter does.
  `converters.BINARY_WRITE_REASON` is the one refusal, shared by the gateway and by `tools.mutations.resolve_text_target`, which every text-writing surface resolves through so the refusal lands before the approval prompt rather than after the run.
  `.scratch/` answers to no format at all, checked before the seam here as everywhere else.
- `move_document` and `delete_document` are the agent's other two mutations, wired in `agents/tools/write.py` like the write tools.
  A move is the one mutation with two ends, so it routes through `store.scoped_pair_operation`, announces both scopes with `workspace_events.announce_paths`, and puts both paths in a single `ApprovalRequired`; it relocates an entry, so neither end may be `.scratch/`.
  A delete does reach `.scratch/`, where it is the unlink and nothing else, so a run can clear the state it created.
- Tables are queried (`query_table`, Polars SQL) and JSON filtered (`jq`) against the original file, never read, and `converters.TABULAR_SUFFIXES` / `converters.JSON_SUFFIXES` are the one table behind each split, which `read_document` points at.
  The pointer follows the entry, not the path in hand (`tools.base.query_hint`): an uploaded table is served as its `<stem>.md` projection, so the read that needs the hint most never names a tabular suffix, and `sidecar_hint` names the same tool ahead of the extracted text when the original itself is refused.
  The read tools still serve the markdown projection, which retrieval and citation anchors are built on.
- Every bulk-output tool declares an `output_path` that stores the result in the workspace and returns a receipt, and that write answers to the same approval gate as the write tools.
- A tool is in the model's initial context or it is not registered: nothing is deferred, MCP servers included.
  `defer_loading` hid a tool from the tool list and offered it back through tool search, which bought nothing here (`llm.py` builds only `OpenAIChatModel`, which renders no wire-level deferral) and cost a model that read a pointer at a hidden tool as naming one it was not given.
- `settings.tools.disabled` is what replaces it: an operator's tool names, in the same namespace as a request's `disabled_tools` and unioned with it into the one `PrepareTools` pass, which reaches MCP tools too.
  It defaults to the two conversation tools, and `agents.check_tool_settings` fails the boot on a name no tool has.
- See `backend/README.md` for the typing pass, the budgets, the redirect channels, and why nothing is deferred.

### Python sandbox

- `run_python` is a Monty sandbox with no network or host filesystem: the workspace is a read-only mount (`tools/workspace_os.py`, routing through the same seams as the read tools so the `DocumentFilter` stays one predicate), `.scratch/` is the only writable path, and a document is written only by committing `/out` through the canonical write path after the program succeeds.
  The declared `commit_path` is a second name for `/out` inside the program (`WorkspaceOS.output`, seeded with the document as it stands), so the two names are one file and not a conflict, while a write to any other document stays refused.
  It is named `commit_path` and not `output_path` because the redirect argument of that name on every other tool captures the call's result, and a model carrying that meaning over declares a path, returns the document it computed, and writes nothing: the two mean different things, so they are spelled differently rather than disambiguated in prose on every request.
  There is one path grammar and the sandbox does not add to it: a program opens `~/notes.md`, the path every tool result, citation, and argument spells, while a leading slash is the run's own files (`/tmp`, `/out`).
  `WORKSPACE_MOUNT` (`/workspace/~/notes.md`) is recognised inside a program but never produced, since a model brings the convention from elsewhere; a tool argument keeps the one grammar, so the approval gate and the commit never read a path differently.
  The mount is why `open` is the read tools, `re` is grep, and `json` is jq, so a host function must never be a second way to do one of those.
  What is injected (`tools/monty.py`, gated in `agents/tools/compute.py`) is only what a program cannot do itself, and a tool says so with `Tool.injectable`: the set is filtered from the very lists that register the tools, so it cannot drift from them, and each is injected exactly when the tool of the same name is live, since a withheld tool must not come back as a function.
  Nothing that mutates is injected and nothing can be, since a running program cannot stop for approval.
  A program gets the structured `data` channel as the objects `to_jsonable_python` makes of it, the same serialiser a `.json` `output_path` writes with, and the declarations come from pydantic-ai's `FunctionSignature`: once for the model (`declarations`), once as `type_check_stubs` (`stubs`), which `settings.sandbox.type_check` enforces.
- `settings.tools.sandbox_only` is the third answer to where a tool lives: injected as a function and withheld from the tool list, limited to `agents.tools.INJECTABLE_TOOL_NAMES`, and overridden by `disabled`.
  `unlisted_tool_names` answers what the schema pass drops; what the sandbox withholds is a different question, answered in `sandbox_surface` itself rather than published beside it under a near-twin name.
  A tool suits it when its schema dwarfs its declaration and its result is nearly always an input to further work, and is ruled out either by a consumer of its tool part (a citation source is registered off one, and a call inside a program emits none) or by a pointer naming it, since following a pointer to a name carrying no schema leaves the model one indirection short of the call, which is what `defer_loading` was removed for.
  A tool clearing all three is registered and injected both.
  See `backend/README.md` for the mount, the budgets, what a program may import, and why the type checker needs `open` declared.

## Testing

- Every test must be stateless: only a temporary filesystem (`tmp_path`, the `data_dir`/`user_store` fixtures) and module state guarded by `monkeypatch`.
- No test may reach a live database or other stateful service.
  Stub the paths that would hit PostgreSQL with `monkeypatch` (e.g. `chunk_and_index_document` to a no-op).
- The live-DB surface (migrations, the running server, retrieval) is covered by manual smoke tests and the dev stack.
- Run `uv run pytest` for the backend and `npm test` for the frontend, both without external services.
