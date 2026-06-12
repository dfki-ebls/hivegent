# Hivegent

Agentic system for non-expert users to interact with LLMs guided by experience.

## TODO

### Snapshot the system prompt on `Conversation`

Pydantic AI's `instructions=` (passed in `server/routes/conversations.py`) is re-applied per request and never lands in `result.all_messages()`, so the personality template, citation/image/math blocks, plan-mode addendum, and injected memory content are absent from `Message.payload`.
Everything else worth recording (retrieved chunks, model name, tool calls, completions) is already in the dumped `ModelMessage` payload and recoverable with `json_extract`, so no new tables are needed.

1. Add a nullable `instructions: Mapped[str | None]` column to `Conversation` in `db/models.py` via an Alembic migration.
2. Plumb the resolved `instructions` string from the chat route into `replace_messages`.
3. In `replace_messages`, write the column once on conversation creation (the lazy-create branch where `conv is None`); leave it untouched on subsequent turns.
4. Known limitation: mid-conversation drift (e.g. memory updates between turns) is not captured; graduate to per-turn snapshots only if a concrete need arises.
