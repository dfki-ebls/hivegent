# Hivegent Backend

## Database migrations

Schema changes are managed by [Alembic](https://alembic.sqlalchemy.org/).
The migration tree lives inside the package at `src/hivegent/migrations/`, so revisions ship with the wheel and the same files drive both development and production.

### Runtime behaviour

`apply_migrations()` (`hivegent.db.migrations`) calls `alembic upgrade head` programmatically and is wired into the FastAPI lifespan in `server/app.py`.
Every time the API server starts it brings the schema to head before accepting traffic; no separate deploy step is required.
The test suite goes through the same path via the `db_initialized` fixture in `tests/conftest.py`, so tests exercise exactly the migrations production runs.

### Generating a new revision

Whenever you change `src/hivegent/db/models.py`, generate a matching revision in the same commit:

```sh
cd backend
uv run alembic revision --autogenerate -m "describe the change"
```

The autogenerate runs against the configured `HIVEGENT_DB__URL`.
For a clean diff against an empty database, point it at a throwaway SQLite file:

```sh
HIVEGENT_DB__URL="sqlite+aiosqlite:///$(mktemp -t hivegent-XXXXXX.db)" \
  uv run alembic revision --autogenerate -m "describe the change"
```

Review the generated file in `src/hivegent/migrations/versions/`.
Autogenerate is reliable for table/column/index changes but misses anything dialect-specific (partial indexes, server defaults, raw `op.execute(...)` data backfills) — edit the file by hand when needed.

Verify there is no drift left over before committing:

```sh
uv run alembic check
```

The constraint naming convention on `Base.metadata` produces stable names like `pk_users`, `fk_documents_owner_user_id_users`, etc.
SQLite ALTERs go through Alembic's batch mode automatically (configured in `env.py`).

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

### Switching from SQLite to PostgreSQL

The schema is dialect-neutral.
To switch backends, change `HIVEGENT_DB__URL` (e.g. `postgresql+psycopg://…`) and start the server — the lifespan applies the same migrations against the new database.

### Production (NixOS / systemd)

The systemd unit shipped by [`raise-infra`](../../raise-infra/nixos/options/hivegent.nix) runs `hivegent serve`, and the lifespan handler then runs the migrations during startup.
For the local Postgres case (`custom.hivegent.postgresql.createLocally = true`), the unit is ordered after `postgresql.target`, so the database role and the `hivegent` database exist before migrations run.
A failed migration aborts startup with a non-zero exit code, which trips the unit's `Restart = "on-failure"` policy and surfaces in `journalctl -u hivegent`.
