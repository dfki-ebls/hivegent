"""CLI for Hivegent host operations: serving, migrating, reindexing, exporting, and resetting.

It acts on the configured data directory and database directly, with the
privileges of the host it runs on, and never over HTTP.
"""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from .config import sanitize_user_id

__all__ = ["app"]

app = typer.Typer(
    help="Hivegent host operations: serve, dev, migrate, reindex, export, and reset.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.callback()
def _main() -> None:
    """Configure logging once for every CLI command (the CLI entry point).

    The server worker configures itself in ``create_app`` instead, because
    uvicorn's ``--reload`` child runs the factory without ever entering this
    callback.
    """
    from .logging_config import configure_logging

    configure_logging()


@app.command()
def migrate(
    revision: Annotated[
        str,
        typer.Argument(
            help="Revision to upgrade to; defaults to 'head'. Pass a specific "
            "hash to stop at that point."
        ),
    ] = "head",
) -> None:
    """Upgrade the configured database with Alembic's migrations.

    The API server upgrades the database automatically on startup, so this
    command is only needed for ad-hoc operator use, such as applying
    migrations in a maintenance window.  It never downgrades.
    """
    from alembic import command

    from .db.migrations import build_alembic_config

    command.upgrade(build_alembic_config(), revision)


def _on_host[T](run: Callable[[], Awaitable[T]], *, exclusive: bool = True) -> T:
    """Run *run* on the configured database, while no server runs on it when *exclusive*, ending the command on a refusal."""
    from contextlib import nullcontext

    from .db import engine_lifespan
    from .db.engine import ServerRunning, no_server_running

    async def hosted() -> T:
        async with (
            engine_lifespan(),
            no_server_running() if exclusive else nullcontext(),
        ):
            return await run()

    try:
        return asyncio.run(hosted())
    except (ServerRunning, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def reindex(
    workspace: Annotated[
        str | None,
        typer.Option(
            help="Key of the one workspace to reindex, such as 'user:alice' or 'global'."
        ),
    ] = None,
) -> None:
    """Chunk and embed the documents again at the next start, such as to recover a workspace.

    The index follows the options of its chunkers and its embedding model by
    itself, and an update whose code chunks or embeds otherwise ships a
    migration that clears the stamps, so this is for ad-hoc use only.  It
    clears the stamps of the documents, which the next start indexes again,
    and refuses while a server runs on the database.
    """
    from .db.documents import clear_content_states

    count = _on_host(lambda: clear_content_states(workspace))
    typer.echo(f"Marked {count} documents to index again at the next start.")


async def _export(owner: str, output: Path) -> None:
    """Write everything the user *owner* owns to *output* (:func:`~hivegent.server.export.build_export`)."""
    import shutil

    from .db.users import user_exists
    from .server.export import ExportPart, build_export
    from .store import User

    user = User(id=sanitize_user_id(owner))

    if not await user_exists(user.id):
        raise ValueError(f"Unknown user: {owner!r}")

    with (
        await build_export(user, frozenset(ExportPart)) as spool,
        output.open("wb") as out,
    ):
        shutil.copyfileobj(spool, out)


@app.command()
def export(
    owner: Annotated[str, typer.Argument(help="The id of the user to export.")],
    output: Annotated[
        Path, typer.Argument(help="The ZIP archive to write.", dir_okay=False)
    ],
) -> None:
    """Export everything one user owns as a ZIP archive, such as to answer a request for their data.

    It only reads, so it runs beside a server.  Users export their own data
    in the UI, in parts or all at once.
    """
    _on_host(lambda: _export(owner, output), exclusive=False)
    typer.echo(f"Exported {owner} to {output}.")


class ResetTarget(StrEnum):
    """What ``hivegent reset`` wipes."""

    WORKSPACE = "workspace"
    DATABASE = "database"
    FACTORY = "factory"
    USER = "user"
    GROUP = "group"
    SOURCES = "sources"


_OWNED = frozenset({ResetTarget.USER, ResetTarget.GROUP})


async def _reset_owner(target: ResetTarget, owner: str) -> None:
    """Wipe everything the user or group *owner* owns.

    A user loses their workspace, skills, conversations, memory, and identity
    row, which a later sign-in creates again, a group its workspace and
    skills.  The user is built here with no groups
    or roles, as the operator acts on the host and the token that would carry
    them is never held.
    """
    from .db.groups import list_group_ids
    from .db.users import user_exists
    from .server.common import delete_scope_data, delete_user_data
    from .skills import LibraryGrant, library
    from .store import GroupScope, HostGrant, User

    if target is ResetTarget.GROUP:
        granted = LibraryGrant(HostGrant().workspace(GroupScope(owner)))
        # A group's row only comes with its first document, its libraries on disk.
        folders = (granted.store.path, library(granted.scope).path)

        if not any(folder.exists() for folder in folders) and (
            owner not in await list_group_ids()
        ):
            raise ValueError(f"Unknown group: {owner!r}")

        await delete_scope_data(granted)
        return

    user = User(id=sanitize_user_id(owner))

    if not await user_exists(user.id):
        raise ValueError(f"Unknown user: {owner!r}")

    await delete_user_data(user)


async def _reset(target: ResetTarget, owner: str | None) -> None:
    """Wipe *target* in the configured data directory and database."""
    from .config import settings
    from .conversation_files import reset_conversation_files
    from .db.sources import prune_sources
    from .db.users import delete_all_identities
    from .db.workspaces import delete_all_workspaces
    from .files import remove_path
    from .skills import remove_skills_root
    from .workspace import delete_workspace_root

    if owner is not None:
        await _reset_owner(target, owner)
        return

    if target is ResetTarget.SOURCES:
        from .sources.sync import prune_sources as drop_unconfigured

        await drop_unconfigured(keep_held=False)
        return

    if target is not ResetTarget.DATABASE:
        await delete_workspace_root()

    if target is ResetTarget.FACTORY:
        await remove_skills_root()

    await delete_all_workspaces()
    # Their mirrors' rows went, so every source syncs afresh into an empty mirror.
    _ = await prune_sources((), keep_held=False)
    await asyncio.to_thread(remove_path, settings.data_dir / "sources")

    if target is not ResetTarget.WORKSPACE:
        await reset_conversation_files()
        await delete_all_identities()


@app.command()
def reset(
    target: Annotated[
        ResetTarget,
        typer.Argument(
            help="'workspace' drops every file and document, 'database' every "
            "user, group, and conversation, 'factory' both and every skill, "
            "'user' and 'group' everything one user or group owns, "
            "'sources' every source no longer configured with its mirror, "
            "also one the boot keeps for the files its source lost."
        ),
    ],
    owner: Annotated[
        str | None,
        typer.Argument(help="The id of the user or group to wipe, for those alone."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
) -> None:
    """Wipe data on this host that no HTTP route wipes for anyone but its owner.

    Runs against the configured data directory and database directly, and
    refuses while a server runs on that database, whose caches and open
    streams would outlive the wipe.
    'database' deletes every workspace row, which takes every document and
    chunk along, and cascades from users and groups to memory and
    conversations, but leaves the workspace files, which reconciliation then
    indexes again.  Users wipe their own data in the UI.
    """
    if (owner is None) == (target in _OWNED):
        typer.echo(
            "Name the id of a 'user' or 'group' wipe, and of no other.", err=True
        )
        raise typer.Exit(1)

    what = f"{target} {owner}" if owner is not None else f"the {target}"

    if not yes and not typer.confirm(f"Irreversibly reset {what}?"):
        raise typer.Abort()

    _on_host(lambda: _reset(target, owner))

    typer.echo(f"Reset {what}.")


Port = Annotated[int, typer.Option("--port", "-p", help="Port to bind to")]
Reload = Annotated[bool, typer.Option("--reload", "-r", help="Enable auto-reload")]


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "127.0.0.1",
    port: Port = 8000,
    reload: Reload = False,
) -> None:
    """Start the Hivegent API server, which refuses to start without an issuer."""
    _run("create_app", host, port, reload)


@app.command()
def dev(port: Port = 8000, reload: Reload = False) -> None:
    """Start the API server on the loopback address, every caller acting as the development user.

    It validates no token and refuses to start with an issuer, so a deployment,
    which runs ``serve``, never has the development user.
    """
    _run("create_dev_app", "127.0.0.1", port, reload)


def _run(factory: str, host: str, port: int, reload: bool) -> None:
    import uvicorn

    uvicorn.run(
        f"hivegent.server:{factory}",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        reload_dirs=str(Path(__file__).parent) if reload else None,
        # No uvicorn dictConfig: its loggers propagate to the root handler
        # installed by configure_logging, so access/error lines share the app's
        # format instead of uvicorn's own.
        log_config=None,
        timeout_keep_alive=300,
        # Must stay below the systemd/dinit stop timeout so uvicorn force-closes
        # and runs lifespan teardown before the supervisor resorts to SIGKILL.
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    app()
