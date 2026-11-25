"""Source repository: the sync state of the external sources.

A source's row holds its cursor, how its last sync went, and the sync an
admin requested.  Its items map each file of the source to its path in the
mirror and its version, and note whether the deletion of the file is held.
They are written in the transaction of the changeset that lands those files,
so the items never disagree with the mirror's rows, and as arrays, so no number of
files outgrows the driver's placeholders.  All of it is rebuilt by a full
sync, so losing it costs a download, never content.
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import (
    BindParameter,
    String,
    and_,
    any_,
    bindparam,
    case,
    delete,
    func,
    literal,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..shared.connectors import RemoteItem
from ._common import ensure_row
from .engine import session
from .models import Source, SourceItem

__all__ = [
    "Due",
    "Request",
    "SourceState",
    "drop_items",
    "due",
    "ensure_source",
    "finish_sync",
    "load_held",
    "load_items",
    "load_states",
    "prune_sources",
    "put_items",
    "request_sync",
    "set_held",
]

type Request = Literal["sync", "confirm"]
"""A sync of a source, which a confirm also lists in full and deletes what was held."""


@dataclass(frozen=True, slots=True)
class SourceState:
    """Where a source's next delta starts, how its last sync went, and what it mirrors and holds."""

    cursor: str | None = None
    synced_at: datetime | None = None
    error: str | None = None
    requested: Request | None = None
    held: int = 0
    items: int = 0


def _state(row: Source, held: int = 0, items: int = 0) -> SourceState:
    return SourceState(
        row.cursor,
        row.synced_at,
        row.error,
        _request(row.requested),
        held,
        items,
    )


def _request(value: str | None) -> Request | None:
    return None if value is None else "confirm" if value == "confirm" else "sync"


async def ensure_source(s: AsyncSession, source_id: str) -> None:
    """Materialise a :class:`Source` row lazily before a dependent insert."""
    await ensure_row(s, Source, id=source_id)


def _item(row: SourceItem) -> RemoteItem:
    return RemoteItem(id=row.remote_id, path=row.path, version=row.version)


async def load_states(*, counted: bool = False) -> dict[str, SourceState]:
    """Every source's state by its id, with the files it mirrors and holds when *counted*."""
    async with session() as s:
        if not counted:
            return {row.id: _state(row) for row in await s.scalars(select(Source))}

        counts = (
            select(
                SourceItem.source_id,
                func.count().label("items"),
                func.count().filter(SourceItem.held).label("held"),
            )
            .group_by(SourceItem.source_id)
            .subquery()
        )
        rows = await s.execute(
            select(
                Source,
                func.coalesce(counts.c["held"], 0),
                func.coalesce(counts.c["items"], 0),
            ).outerjoin(counts, counts.c.source_id == Source.id)
        )

        return {row.id: _state(row, held, items) for row, held, items in rows.tuples()}


async def load_items(source_id: str) -> list[RemoteItem]:
    """Every file *source_id* mirrors."""
    async with session() as s:
        rows = list(
            await s.scalars(select(SourceItem).where(SourceItem.source_id == source_id))
        )

    return [_item(row) for row in rows]


async def load_held(source_id: str) -> set[str]:
    """The ids of the files of *source_id* whose deletion is held."""
    async with session() as s:
        return set(
            await s.scalars(
                select(SourceItem.remote_id).where(
                    SourceItem.source_id == source_id, SourceItem.held
                )
            )
        )


def _texts(values: Iterable[str]) -> BindParameter[Sequence[str]]:
    """One array parameter for any number of values, however many the driver's placeholders could hold."""
    return bindparam(None, list(values), type_=ARRAY(String))


async def put_items(
    s: AsyncSession, source_id: str, items: Sequence[RemoteItem]
) -> None:
    """Record in the caller's transaction that each of *items* lies at its path at its version, displacing whatever lay there.

    The items name distinct files at distinct paths.
    """
    if not items:
        return

    await ensure_source(s, source_id)
    rows = (
        func.unnest(
            _texts(item.id for item in items),
            _texts(item.path for item in items),
            _texts(item.version for item in items),
        )
        .table_valued("remote_id", "path", "version")
        .render_derived()
    )
    _ = await s.execute(
        delete(SourceItem).where(
            SourceItem.source_id == source_id,
            SourceItem.path == rows.c.path,
            SourceItem.remote_id != rows.c.remote_id,
        )
    )
    insert = pg_insert(SourceItem).from_select(
        ["source_id", "remote_id", "path", "version"],
        select(literal(source_id), rows.c.remote_id, rows.c.path, rows.c.version),
    )
    _ = await s.execute(
        insert.on_conflict_do_update(
            index_elements=[SourceItem.source_id, SourceItem.remote_id],
            set_={"path": insert.excluded.path, "version": insert.excluded.version},
        )
    )


async def drop_items(
    s: AsyncSession, source_id: str, remote_ids: Collection[str]
) -> None:
    """Forget the files *remote_ids* of *source_id* in the caller's transaction."""
    if not remote_ids:
        return

    _ = await s.execute(
        delete(SourceItem).where(
            SourceItem.source_id == source_id,
            SourceItem.remote_id == any_(_texts(remote_ids)),
        )
    )


async def set_held(
    s: AsyncSession, source_id: str, remote_ids: Collection[str]
) -> None:
    """Hold the deletion of exactly the files *remote_ids* of *source_id* in the caller's transaction, releasing every other."""
    held = SourceItem.remote_id == any_(_texts(remote_ids))
    _ = await s.execute(
        update(SourceItem)
        .where(SourceItem.source_id == source_id, SourceItem.held != held)
        .values(held=held)
    )


async def request_sync(source_id: str, *, confirm: bool) -> None:
    """Ask for a sync of *source_id*, a confirm outranking one that is not.

    Each request counts, so the sync running meanwhile leaves it for the next
    one, except a plain request while a confirm is requested, which lists in full anyway.
    """
    absorbed = and_(literal(not confirm), Source.requested == "confirm")

    async with session() as s:
        await ensure_source(s, source_id)
        _ = await s.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(
                requested="confirm"
                if confirm
                else func.coalesce(Source.requested, "sync"),
                request_seq=case(
                    (absorbed, Source.request_seq), else_=Source.request_seq + 1
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class Due:
    """A sync a source wants, with the count of requests it answers."""

    request: Request
    request_seq: int
    """The ``request_seq`` it found, which a request made meanwhile changes."""


async def due(source_id: str, *, interval: timedelta) -> Due | None:
    """The sync *source_id* wants, if one is requested or its last ended *interval* ago."""
    async with session() as s:
        await ensure_source(s, source_id)
        row = (
            await s.execute(
                select(Source.requested, Source.request_seq).where(
                    Source.id == source_id,
                    or_(
                        Source.requested.is_not(None),
                        Source.synced_at.is_(None),
                        Source.synced_at <= func.now() - interval,
                    ),
                )
            )
        ).one_or_none()

    return None if row is None else Due(_request(row.requested) or "sync", row.request_seq)


async def finish_sync(
    source_id: str, due: Due, *, cursor: str | None, error: str | None
) -> None:
    """Record how the sync of *source_id* ended.

    It clears the request it answered, while a request made meanwhile stays for the next sync.
    """
    async with session() as s:
        _ = await s.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(
                cursor=cursor,
                error=error,
                synced_at=func.now(),
                requested=case(
                    (Source.request_seq == due.request_seq, None),
                    else_=Source.requested,
                ),
            )
        )


async def prune_sources(
    configured: Iterable[str], *, keep_held: bool
) -> tuple[list[str], list[str]]:
    """Drop every source no longer configured, its documents and items along.

    With *keep_held*, a source keeps its rows while it holds a file its source
    lost, since the mirror is then the only copy, and a configuration that lost
    the source by mistake loses nothing.

    Returns:
        The ids of the dropped sources and of those kept for their held files.
    """
    unconfigured = Source.id.not_in(list(configured))
    holding = Source.id.in_(select(SourceItem.source_id).where(SourceItem.held))

    async with session() as s:
        gone = await s.scalars(
            delete(Source)
            .where(unconfigured, ~holding if keep_held else true())
            .returning(Source.id)
        )
        dropped = list(gone)
        kept = await s.scalars(select(Source.id).where(unconfigured))

        return dropped, list(kept)
