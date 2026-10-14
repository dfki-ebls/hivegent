# RAG System

- Integration through Vercel AI Data Stream Protocol implemented by Vercel AI SDK UI in frontend and Pydantic AI in backend
- Project has not yet been deployed, don't care about breaking changes and migrations, prefer clean solutions
- `ARCHITECTURE.md` holds the system shape, `backend/README.md` and `frontend/README.md` the design decisions behind each half
- Every test must be stateless and reach no live database or other stateful service, so stub the paths that would hit PostgreSQL with `monkeypatch`
- Run `uv run pytest` for the backend and `npm test` for the frontend, both without external services

## Frontend

- SPA React app with Typescript
- Vercel AI SDK UI for user interaction
- shadcn/ui on Base UI for styling and components, with the chat built from its AI Elements patterns (Trace, Message, Code Block, ...)
- Never modify the generated files of `npm run shadcnup` (`components/ui`, `hooks/use-highlighted-code.ts`, `hooks/use-mobile.ts`, `lib/highlighter.tsx`), compose them instead

## Backend

- FastAPI server for handling requests and responses
- Pydantic AI for LLMs and agents
- PostgreSQL with `pgvector` as the database
- Everything under `tools`, `chunkers`, `converters` needs to stay self-contained
