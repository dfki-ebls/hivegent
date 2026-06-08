# RAG System

- Integration through Vercel AI Data Stream Protocol implemented by Vercel AI Elements in frontend and Pydantic AI in backend
- Development environment with Nix Flakes
- Project has not yet been deployed, don't care about breaking changes and migrations, prefer clean solutions

## Frontend

- SPA React app with Typescript
- Vercel AI SDK UI for user interaction
- shadcn/ui and Vercel AI Elements for styling and components

## Backend

- FastAPI server for handling requests and responses
- Pydantic AI for LLMs and agents
- Workspace mutations must go through the API; files dropped into `data/workspace/` by hand are reconciled into SQL only on startup.
- The single supported database backend is PostgreSQL with the `pgvector` extension; dev/test sessions reach it over the Unix socket exposed by `services-flake` under `data/db/`.
- Chunk metadata, text, and vectors live together in the `chunks` table and cascade with their owning document — there is no separate vector-index layer to reconcile.
- Repository writes that create or upsert a row must be atomic, never a read-then-write (`s.get`/`_find` then `s.add`): use `INSERT ... ON CONFLICT` (e.g. the `ensure_row` helper in `db/_common.py`) or a transaction-scoped advisory lock for multi-row sequences — see `backend/README.md` for the convention and rationale.
- Schema is managed by Alembic, see `backend/README.md` for the workflow. Whenever you touch `backend/src/hivegent/db/models.py`, generate a matching revision in the same change (`uv run alembic revision --autogenerate -m "<summary>"`) so the database and the models stay in sync.

## Testing

- Every test must be stateless: it may touch only a temporary filesystem (`tmp_path`, the `data_dir`/`user_store` fixtures) and module-level Python state guarded by `monkeypatch`.
- No test may connect to a live database or any other stateful service; code paths that would hit PostgreSQL must be stubbed with `monkeypatch` (e.g. patch `chunk_and_index_document` to a no-op) rather than exercised against a running instance.
- The live-DB surface (Alembic migrations, the running server, retrieval) is covered by manual smoke tests and the dev stack, not by the automated suite.
- Run the backend suite with `uv run pytest` and the frontend suite with `npm test`; both run without any external services.
