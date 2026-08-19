# RAG System

- Integration through Vercel AI Data Stream Protocol implemented by Vercel AI Elements in frontend and Pydantic AI in backend
- Development environment with Nix Flakes
- Project has not yet been deployed, don't care about breaking changes and migrations, prefer clean solutions
- Model structured data with a fixed set of keys as a frozen dataclass, Pydantic model, or `TypedDict` (TypeScript: an `interface` or `type`), never a bare `dict[str, Any]`, so the shape is type-checked.

## Frontend

- SPA React app with Typescript
- Vercel AI SDK UI for user interaction
- shadcn/ui and Vercel AI Elements for styling and components

## Backend

- FastAPI server for handling requests and responses
- Pydantic AI for LLMs and agents
- The filesystem under `data/workspace/` is the source of truth for document content; the `documents` and `chunks` rows are an index derived from it.
- Workspace mutations should go through the API; markdown and plain-text files dropped or edited by hand are ingested into SQL on startup (a plain-text original has its `<stem>.md` derived for it), files needing a converter stay inert on disk (visible, deletable, but never chunked) until uploaded or reconverted, and reconciliation never deletes workspace files.
- Every change to a workspace reaches the client on the per-owner SSE feed, which the frontend turns into a refresh of the named scope.
  Long-running work is a real job and the settled `document.*` job carries it; a mutation that ran inline never was a job, so it publishes a `ScopeChanged` event instead via `workspace_events` (`notify_workspace_change` for a caller holding the store, `announcing_mutator` for the write tools, which hold only a canonical path).
  Any surface that mutates without submitting a job must notify, or every client but the one that asked stays stale, and a write is never inferred from the chat transcript.
  A browser tab names itself with `X-Client-Id` on every request and the notification skips that tab, so the client that asked keeps its own read-after-write (which still works with the feed down) while the user's other tabs learn from the feed, and neither reads the workspace twice.
  Delivery is per-owner, so a group workspace refreshes for the writer, not yet for the other members.
  A `ScopeChanged` event is transient and never retained, so the feed carries only what happens while it is open: the client closes that gap itself by re-reading every scope it already holds on each handshake (`onFeedReady`), which is what covers a mutation that landed while it was disconnected.
- A document is writable exactly when it is readable as text: the write tools accept markdown descriptions and plain-text originals (regenerating the original's markdown projection through the upload pipeline) and reject binaries, which are replaced by uploading instead.
- A chat turn attaches images and nothing else, gated on `converters.INGESTIBLE_IMAGE_MEDIA_TYPES`, the media types every vision backend ingests identically, so no `BinaryContentMode` policy and no conversion runs on the chat's latency budget.
  Anything else belongs in a workspace, whose upload pipeline converts, chunks, and indexes it once for retrieval rather than spending context on it every turn, since an attachment is stored once but re-sent to the model on each later turn of the conversation.
  `/settings` serves the same table and the size cap to the client as `AttachmentLimits`, so the file picker refuses what the chat route would refuse and a rejection costs no round trip.
- The mutating tools span the personal workspace plus the groups the user may write to (the read tools span every readable one) and route each accepted path back to the workspace its `~` / `@<group>` prefix names via `store.scoped_operation`, so a path copied verbatim out of a listing is written where it says it is.
- There is no working directory: a document path is always full and prefixed, and where a new document goes is the model's choice, stated only by the path it passes (missing subdirectories are created). Every path tool refuses a path naming no known root with the addressable roots named (`tools.base.workspace_root_hint`) rather than resolving it against some default.
- A group is identified by the ID its OIDC groups claim carries, and by nothing else: it keys the `groups` row, the `documents.owner_group_id` FK, the `group:<id>` workspace directory, and the `@<id>` prefix of every canonical path. `auth.parse_group_claims` is the one place that knows how a provider spells that claim — the SCIM object of RFC 7643 §4.1.2 that RFC 9068 §2.2.3.1 names for it (`{"value", "display"}`), or a bare string, which has no ID to recover and so doubles as its own. A provider's display name is carried to the client as a label only (`GroupInfo`, `User.group_labels`) and never reaches a path or a row, so renaming a group changes what the UI shows and nothing else — while renaming one on a bare-string provider strands its workspace, since the new name is a new ID.
- The single supported database backend is PostgreSQL with the `pgvector` extension; dev/test sessions reach it over the Unix socket exposed by `services-flake` under `data/db/`.
- Chunk metadata, text, and vectors live together in the `chunks` table and cascade with their owning document — there is no separate vector-index layer to reconcile.
- Never read user-supplied content with `Path.read_text`: every such read goes through `text.read_text_file` / `text.decode_bytes`, which support Unicode and Western Windows text, return `None` for binary-looking content, and report the source encoding so a transcode is never silent. Writes are always UTF-8.
- Every path string _entering_ the system is folded to Unicode NFC by `config.normalize_unicode`, at two boundaries: `sanitize_document_path` for the HTTP and workspace API, and `tools.base.resolve_search_path` / `scope_paths` (plus `_glob_entries` for the one path argument that carries no scope prefix) for tool arguments. macOS hands out decomposed filenames while a model can only emit precomposed ones, so without this the same visible name is two different files on Linux. File _content_ is never normalized: `content_digest` fingerprints the decoded text verbatim, so folding it would invalidate every stored digest and `expected_hash` token.
- That makes new content NFC by construction, but does _not_ hold for content written to `data/workspace/` out of band: the reconcile walk copies whatever spelling is on disk into `documents.stem_path`, deliberately, since normalizing only the SQL side would split it from the file and `_sweep_sql_orphans` would then delete the row and its chunks. Disk and SQL are repaired together by `workspace.normalize_workspace_paths`, which renames the files and rewrites the stems in one pass. `reconcile_store` calls it before the ingest, so it runs at startup and behind `POST /admin/reindex`, wherever a hand-dropped file is picked up. It has to lead: the ingest copies the on-disk spelling into `stem_path` verbatim, so a decomposed entry would be left matching no inbound path at all, unreadable and undeletable, with the next write landing beside it as a duplicate. It is idempotent, so a canonical workspace costs one walk and no renames. A migration cannot do this, because SQL alone cannot rename the files.
- Repository writes that create or upsert a row must be atomic, never a read-then-write (`s.get`/`_find` then `s.add`): use `INSERT ... ON CONFLICT` (e.g. the `ensure_row` helper in `db/_common.py`) or a transaction-scoped advisory lock for multi-row sequences — see `backend/README.md` for the convention and rationale.
- The tool implementations under `backend/src/hivegent/tools/` must not read the application settings (`hivegent.config.settings`): every tunable is an instance variable (dataclass field) with a sensible default, and the settings are applied only where tools are instantiated for an agent or MCP endpoint (`agents/tools/*`, `mcp/tools/*`). Importing sibling infrastructure such as `converters`, `chunkers`, `subprocesses`, or `security` is fine.
- Schema is managed by Alembic, see `backend/README.md` for the workflow. Whenever you touch `backend/src/hivegent/db/models.py`, write a matching revision by hand under `backend/src/hivegent/migrations/versions/` in the same change so the database and the models stay in sync. Revisions cannot be autogenerated (that requires a live database), so derive the operations from the actual model changes, following the structure of the existing revision files.

## Testing

- Every test must be stateless: it may touch only a temporary filesystem (`tmp_path`, the `data_dir`/`user_store` fixtures) and module-level Python state guarded by `monkeypatch`.
- No test may connect to a live database or any other stateful service; code paths that would hit PostgreSQL must be stubbed with `monkeypatch` (e.g. patch `chunk_and_index_document` to a no-op) rather than exercised against a running instance.
- The live-DB surface (Alembic migrations, the running server, retrieval) is covered by manual smoke tests and the dev stack, not by the automated suite.
- Run the backend suite with `uv run pytest` and the frontend suite with `npm test`; both run without any external services.
