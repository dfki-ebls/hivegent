"""The vector search scope and the chunk writes, compiled but never run."""

from collections.abc import Collection, Mapping
from uuid import UUID

import sqlalchemy as sa
from cbrkit.indexable import Pgvector, Unscoped
from exar import ingest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

from hivegent import retrieval
from hivegent.db.documents import accessible_chunks
from hivegent.db.models import Chunk
from hivegent.reach import Reach
from hivegent.store import GlobalScope, GroupScope, User, UserScope
from hivegent.types import DocumentFilter
from tests.helpers import doc, folder

_USER = User("u", read_groups=frozenset({"team"}))
_DOCUMENT = UUID(int=1)


def _compiled(clause: sa.ColumnElement[bool]) -> tuple[str, Mapping[str, object]]:
    """*clause* as PostgreSQL renders it, with its bound parameters."""
    compiled = clause.compile(dialect=postgresql.dialect())

    return str(compiled), compiled.params or {}


def test_scope_is_one_subquery_binding_per_store_and_entry() -> None:
    """Owners and hidden entries reach SQL, never one parameter per document."""
    hidden = DocumentFilter(frozenset({folder("drafts"), doc("a/re_port.md")}))
    reach = Reach(_USER, {UserScope("u"): hidden})
    sql, params = _compiled(accessible_chunks(reach))

    assert sql.startswith("chunks.document_id IN (SELECT documents.id")
    assert "NOT (" in sql
    assert params["stem_path_2"] == ["a/re_port", "a/re_port.assets"]
    # LIKE wildcards in a name are escaped, so `_` matches only itself.
    assert params["stem_path_3"] == "a//re/_port.assets//"
    assert len(params) == 9


def test_scope_hiding_every_store_matches_nothing() -> None:
    everything = DocumentFilter(frozenset({folder("")}))
    scopes = (GlobalScope(), GroupScope("team"), UserScope("u"))
    reach = Reach(_USER, dict.fromkeys(scopes, everything))
    sql, _ = _compiled(accessible_chunks(reach))

    assert sql.endswith("WHERE false OR false OR false)")


async def test_index_document_replaces_the_document_s_chunks_by_uuid() -> None:
    calls: list[tuple[sa.ColumnElement[bool], Mapping[UUID, Chunk]]] = []

    class Storage(Pgvector[UUID, Chunk]):
        async def replace_where(
            self, where: sa.ColumnElement[bool], data: Mapping[UUID, Chunk]
        ) -> Collection[UUID]:
            calls.append((where, data))

            return list(data)

    storage = Storage(
        engine=create_async_engine("postgresql+psycopg://"),
        model=Chunk,
        index_type="sparse",
        value_column=Chunk.text.key,
        tsvector_column=Chunk.tsv.key,
        scope=Unscoped.TABLE,
    )
    await retrieval.index_document(
        _DOCUMENT,
        [ingest.Chunk(text="t", start=0, end=1, tokens=1)],
        storage=storage,
    )

    ((where, data),) = calls

    assert _compiled(where) == (
        "chunks.document_id = %(document_id_1)s::UUID",
        {"document_id_1": _DOCUMENT},
    )
    assert all(isinstance(key, UUID) for key in data)
