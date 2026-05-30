# Hivegent Backend

## Concurrency-safe repository writes

Repository functions that create or update a row must do so atomically, never as a non-atomic read-then-write.
A `s.get(...)` (or `_find(...)`) followed by a conditional `s.add(...)` or field assignment is a time-of-check-to-time-of-use race: two requests in separate transactions both observe the row missing, both insert, and all but one fail the primary key or a unique constraint.
The loser's `IntegrityError` rolls back its whole transaction — and when the materialise happens inside a larger write, that silently drops the document, conversation, or memory that triggered it (the original "documents vanish after upload" bug).

Use one of two PostgreSQL-native tools:

- `INSERT ... ON CONFLICT` for single-row inserts and upserts.
  Lazy "materialise on first reference" identity rows use the `ensure_row` helper in `db/_common.py` (`ON CONFLICT DO NOTHING`); see `ensure_user` and `ensure_group`.
  Upserts that overwrite columns use `.on_conflict_do_update(index_elements=[...], set_={...})`, keyed on the relevant primary key or unique constraint; see `db.documents.upsert_document` and `db.memory.save_memory`.
  A core upsert does not fire the ORM `onupdate=_now`, so bump `updated_at` explicitly in `set_` with `func.now()`.
- A transaction-scoped advisory lock (`pg_advisory_xact_lock`) for multi-row read-then-write sequences that no single constraint can guard, such as appending messages at a computed `idx`.
  See `db.conversations.append_messages`, which serialises concurrent turns on the same conversation; the lock auto-releases on commit/rollback.

The schema's constraints are the safety net, not the obstacle: keep the primary keys, unique constraints, and the `documents.single_owner` check, and make the application cooperate with them atomically rather than racing them.
The one accepted exception is a best-effort last-write-wins update with no constraint to violate — e.g. the throttled `last_used_at` bump in `tokens.validate_token` — where serialising would add cost for no correctness gain.

## Database migrations

Schema changes are managed by [Alembic](https://alembic.sqlalchemy.org/).
The migration tree lives inside the package at `src/hivegent/migrations/`, so revisions ship with the wheel and the same files drive both development and production.

### Runtime behaviour

`apply_migrations()` (`hivegent.db.migrations`) calls `alembic upgrade head` programmatically and is wired into the FastAPI lifespan in `server/app.py`.
Every time the API server starts it brings the schema to head before accepting traffic; no separate deploy step is required.
After migrations apply, the lifespan also verifies that the live `chunks.vector` dimension equals `settings.embedding.resolve_dimension()` and refuses to boot on mismatch — a model change without a matching follow-up revision fails loudly at startup, not mid-request.

The unit test tree is hermetic: no test touches a live database.
Live-DB workflows (Alembic, the running server, manual smoke tests) cover the SQL surface end-to-end.

### Generating a new revision

Whenever you change `src/hivegent/db/models.py`, generate a matching revision in the same commit:

```sh
cd backend
uv run alembic revision --autogenerate -m "describe the change"
```

The autogenerate runs against the configured `HIVEGENT_DB__URL`, which must point at a PostgreSQL database with the `pgvector` extension available.
The dev shell sets this automatically to the `services-flake` instance under `data/db/`; start the Postgres service first via `nix run .#watch-dev` or by booting the dev shell, then run autogenerate.

Review the generated file in `src/hivegent/migrations/versions/`.
Autogenerate is reliable for table/column/index changes but misses dialect-specific bits (pgvector index ops, partial indexes, raw `op.execute(...)` data backfills) — edit the file by hand when needed.

Verify there is no drift left over before committing:

```sh
uv run alembic check
```

The constraint naming convention on `Base.metadata` produces stable names like `pk_users`, `fk_documents_owner_user_id_users`, etc.

### Manual migration commands

`hivegent migrate` wraps `alembic.command.upgrade` for ad-hoc operator use (drying a release, applying a specific revision):

```sh
uv run hivegent migrate              # upgrade to head
uv run hivegent migrate <revision>   # upgrade or roll forward to a given revision
```

Standard `alembic` subcommands (`history`, `current`, `downgrade`, `stamp`, …) also work because Alembic auto-discovers the `[tool.alembic]` section in `backend/pyproject.toml`:

```sh
uv run alembic history
uv run alembic current
uv run alembic downgrade -1
```

### Production (NixOS / systemd)

The systemd unit shipped by [`raise-infra`](../../raise-infra/nixos/options/hivegent.nix) runs `hivegent serve`, and the lifespan handler then runs the migrations during startup.
For the local Postgres case (`custom.hivegent.postgresql.createLocally = true`), the unit is ordered after `postgresql.target`, so the database role and the `hivegent` database exist before migrations run.
A failed migration aborts startup with a non-zero exit code, which trips the unit's `Restart = "on-failure"` policy and surfaces in `journalctl -u hivegent`.
