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
- A document is writable exactly when it is readable as text: the write tools accept markdown descriptions and plain-text originals (regenerating the original's markdown projection through the upload pipeline) and reject binaries, which are replaced by uploading instead.
- The mutating tools span the personal workspace plus the groups the user may write to (the read tools span every readable one) and route each accepted path back to the workspace its `~` / `@<group>` prefix names via `store.scoped_operation`, so a path copied verbatim out of a listing is written where it says it is.
- There is no working directory: a document path is always full and prefixed, and where a new document goes is the model's choice, stated only by the path it passes (missing subdirectories are created). Every path tool refuses a path naming no known root with the addressable roots named (`tools.base.workspace_root_hint`) rather than resolving it against some default.
- The single supported database backend is PostgreSQL with the `pgvector` extension; dev/test sessions reach it over the Unix socket exposed by `services-flake` under `data/db/`.
- Chunk metadata, text, and vectors live together in the `chunks` table and cascade with their owning document — there is no separate vector-index layer to reconcile.
- Never read user-supplied content with `Path.read_text`: every such read goes through `text.read_text_file` / `text.decode_bytes`, which support Unicode and Western Windows text, return `None` for binary-looking content, and report the source encoding so a transcode is never silent. Writes are always UTF-8.
- Every path string *entering* the system is folded to Unicode NFC by `config.normalize_unicode`, at two boundaries: `sanitize_document_path` for the HTTP and workspace API, and `tools.base.resolve_search_path` / `scope_paths` (plus `_glob_entries` for the one path argument that carries no scope prefix) for tool arguments. macOS hands out decomposed filenames while a model can only emit precomposed ones, so without this the same visible name is two different files on Linux. File *content* is never normalized: `content_digest` fingerprints the decoded text verbatim, so folding it would invalidate every stored digest and `expected_hash` token.
- That makes new content NFC by construction, but does *not* hold for content written to `data/workspace/` out of band: the reconcile walk copies whatever spelling is on disk into `documents.stem_path`, deliberately, since normalizing only the SQL side would split it from the file and `_sweep_sql_orphans` would then delete the row and its chunks. Disk and SQL are repaired together by `POST /admin/normalize-paths` (`reconcile.normalize_all`), which renames the files and rewrites the stems in one pass. It is idempotent, so re-run it after dropping files in by hand. A migration cannot do this, because SQL alone cannot rename the files.
- Repository writes that create or upsert a row must be atomic, never a read-then-write (`s.get`/`_find` then `s.add`): use `INSERT ... ON CONFLICT` (e.g. the `ensure_row` helper in `db/_common.py`) or a transaction-scoped advisory lock for multi-row sequences — see `backend/README.md` for the convention and rationale.
- The tool implementations under `backend/src/hivegent/tools/` must not read the application settings (`hivegent.config.settings`): every tunable is an instance variable (dataclass field) with a sensible default, and the settings are applied only where tools are instantiated for an agent or MCP endpoint (`agents/tools/*`, `mcp/tools/*`). Importing sibling infrastructure such as `converters`, `chunkers`, `subprocesses`, or `security` is fine.
- Schema is managed by Alembic, see `backend/README.md` for the workflow. Whenever you touch `backend/src/hivegent/db/models.py`, write a matching revision by hand under `backend/src/hivegent/migrations/versions/` in the same change so the database and the models stay in sync. Revisions cannot be autogenerated (that requires a live database), so derive the operations from the actual model changes, following the structure of the existing revision files.

## Testing

- Every test must be stateless: it may touch only a temporary filesystem (`tmp_path`, the `data_dir`/`user_store` fixtures) and module-level Python state guarded by `monkeypatch`.
- No test may connect to a live database or any other stateful service; code paths that would hit PostgreSQL must be stubbed with `monkeypatch` (e.g. patch `chunk_and_index_document` to a no-op) rather than exercised against a running instance.
- The live-DB surface (Alembic migrations, the running server, retrieval) is covered by manual smoke tests and the dev stack, not by the automated suite.
- Run the backend suite with `uv run pytest` and the frontend suite with `npm test`; both run without any external services.
