"""The SQL a ``DocumentFilter`` compiles to hides exactly what the filter hides."""

from collections.abc import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa

from hivegent.db.documents import accessible_documents
from hivegent.db.models import Document, EntryKind, GeneratedBy, Origin
from hivegent.reach import Reach
from hivegent.shared.formats import DOCUMENT_EXTENSION
from hivegent.store import User, WorkspaceScope
from hivegent.types import DocumentFilter
from tests.helpers import doc, folder

USER = User("u", read_groups=frozenset({"team"}))
STORES = USER.workspaces()

STEMS = (
    "a",
    "a.assets/fig1",
    "ab",
    "dir",
    "dir/b",
    "dirx/c",
    "100%_done/d",
    "100xxdone/e",
)

FILTERS = (
    DocumentFilter(),
    DocumentFilter(frozenset({folder("")})),
    DocumentFilter(frozenset({folder("dir")})),
    DocumentFilter(frozenset({doc("a.md")})),
    DocumentFilter(frozenset({doc("a.pdf"), doc("dirx/c.md")})),
    DocumentFilter(frozenset({folder("100%_done")})),
    DocumentFilter(allowed=frozenset()),
    DocumentFilter(allowed=frozenset({folder("")})),
    DocumentFilter(allowed=frozenset({folder("dir"), doc("a.md")})),
    DocumentFilter(
        frozenset({doc("dir/b.md")}), frozenset({folder("dir"), folder("100%_done")})
    ),
)


@pytest.fixture(scope="module")
def engine() -> Iterator[sa.Engine]:
    """Every stem in every store, on an in-memory SQLite database."""
    engine = sa.create_engine("sqlite://")
    Document.metadata.create_all(
        engine, tables=[sa.Table("documents", Document.metadata)]
    )

    with engine.begin() as connection:
        for store in STORES:
            _ = connection.execute(
                sa.insert(Document),
                [
                    {
                        "workspace_key": store.store_key,
                        "stem_path": stem,
                        "entry_kind": EntryKind.USER_MARKDOWN,
                        "origin": Origin.UPLOAD,
                        "generated_by": GeneratedBy.USER,
                        "pipeline": "test",
                        "line_count": 1,
                    }
                    for stem in STEMS
                ],
            )

    yield engine
    engine.dispose()


def _expected(
    engine: sa.Engine, filters: dict[WorkspaceScope, DocumentFilter]
) -> set[UUID]:
    """The ids the Python filters admit among the documents of every store."""
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(Document.id, Document.workspace_key, Document.stem_path)
        ).all()

    by_key = {store.store_key: store.scope for store in STORES}

    return {
        row.id
        for row in rows
        if filters.get(by_key[row.workspace_key], DocumentFilter())(
            row.stem_path + DOCUMENT_EXTENSION
        )
    }


@pytest.mark.parametrize("document_filter", FILTERS)
def test_sql_filter_matches_the_python_filter(
    engine: sa.Engine, document_filter: DocumentFilter
) -> None:
    filters = {STORES[2].scope: document_filter, STORES[1].scope: document_filter}

    with engine.connect() as connection:
        admitted = set(connection.scalars(accessible_documents(Reach(USER, filters))))

    assert admitted == _expected(engine, filters)
