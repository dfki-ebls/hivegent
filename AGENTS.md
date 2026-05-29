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
- The single supported database backend is PostgreSQL with the `pgvector` extension; dev/test sessions reach it over the Unix socket exposed by `services-flake` under `data/hivegent-db/`.
- Chunk metadata, text, and vectors live together in the `chunks` table and cascade with their owning document — there is no separate vector-index layer to reconcile.
- Schema is managed by Alembic, see `backend/README.md` for the workflow. Whenever you touch `backend/src/hivegent/db/models.py`, generate a matching revision in the same change (`uv run alembic revision --autogenerate -m "<summary>"`) so the database and the models stay in sync.
