# Hivegent

Agentic system for non-expert users to interact with LLMs guided by experience.

## TODO

### Wire up Alembic for schema migrations

Today the backend calls `Base.metadata.create_all` in `init_database()`, which is fine for development and CI but a real deployment needs proper migrations.
The `alembic` dependency is already in `pyproject.toml`; the rest is plumbing.

1. Scaffold the migration tree.
Run `uv run alembic init --template async backend/migrations` to generate `alembic.ini` plus the `migrations/` skeleton with an async `env.py`.

2. Point Alembic at our metadata.
In `migrations/env.py`, import `Base` from `hivegent.db.models` and set `target_metadata = Base.metadata`.
Reuse the engine from `hivegent.db.engine` so the URL and PRAGMA hook stay centralised.
The naming convention already lives on `Base.metadata`, so Alembic will pick up stable constraint names automatically.

3. Generate the baseline migration.
With the schema as-is, run `uv run alembic revision --autogenerate -m "baseline schema"`.
Manually review the generated file: the partial index `ix_documents_dirty` carries dialect-specific `sqlite_where` / `postgresql_where` clauses that autogenerate sometimes drops, and the single-owner `CHECK` constraints need to land verbatim.

4. Replace the create-all bootstrap.
Drop `init_database()` from the lifespan and replace it with a small helper that runs `alembic.command.upgrade(config, "head")` via `asyncio.to_thread`.
Tests keep using a fresh upgrade against a temp SQLite file so they exercise the same path production does.

5. Add a drift check to CI.
Run `alembic revision --autogenerate --rev-id _drift_check_ -m _ && git diff --exit-code migrations/versions/` to fail the build if a model change wasn't accompanied by a migration.
Delete the generated stub after the check.

6. Document the deploy step.
The first deployment runs `uv run alembic upgrade head` as a pre-start hook (Kubernetes init container, systemd `ExecStartPre`, Fly.io `release_command`, etc).
Subsequent schema changes follow the same `revision --autogenerate` → review → commit → `upgrade head` loop.

7. Decide on Postgres timing.
The schema is dialect-neutral, so the SQLite-to-Postgres switch is a `HIVEGENT_DB__URL` change plus rerunning `alembic upgrade head` against the new database.
Pick the moment by load and operational appetite, not by feature pressure.
