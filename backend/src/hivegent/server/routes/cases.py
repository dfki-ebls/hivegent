"""Routes for case bases: their similarity configuration and test queries.

A case base is addressed by its canonical workspace path like any document.
Its configuration is inferred from its data in memory (see
:mod:`hivegent.tools.cases`), so reading and querying one write nothing.
"""

import asyncio

from exar.tools import Retry
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, JsonValue
from pydantic_core import to_jsonable_python

from ...auth import CurrentUser
from ...cases import CASE_BASES, bindings
from ...config import settings
from ...reach import Reach
from ...store import User
from ...tools.base import SearchPath
from ...tools.cases import (
    Bindings,
    CaseBase,
    CaseMatch,
    Limits,
    match,
    open_case_base,
    parse_query,
    rank,
)
from ..rate_limit import GENERATION_LIMIT

__all__ = ["router"]

router = APIRouter()


class CaseBaseResponse(BaseModel):
    """A case base with the configuration inferred from its data that a query is ranked by."""

    path: str = Field(description="Canonical path of the case base")
    count: int = Field(description="Number of cases")
    graphs: bool = Field(description="Whether the cases are graphs")
    config: dict[str, JsonValue] = Field(
        description="The configuration inferred from the data, a `cbrkit.spec.Config`"
    )
    query_schema: dict[str, JsonValue] = Field(
        description="JSON Schema of a query, every attribute optional"
    )


class CaseQueryRequest(BaseModel):
    """An example to rank the cases by, exactly one of the three."""

    query: dict[str, JsonValue] | None = Field(
        default=None, description="A partial record, or an inline graph"
    )
    query_path: str | None = Field(
        default=None, description="A BPMN or cbrkit graph file"
    )
    query_key: str | None = Field(default=None, description="A case of the case base")
    limit: int = Field(default=5, ge=1, le=50)


class CaseQueryResponse(BaseModel):
    """The most similar cases, each with its explanation."""

    matches: list[CaseMatch]
    query: JsonValue = Field(
        default=None, description="The query graph, none for a record"
    )


def _limits() -> Limits:
    return Limits(max_bytes=settings.limits.max_file_size_bytes)


def _bad_request(exc: Retry) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _paths(user: User) -> tuple[SearchPath, ...]:
    return Reach(user, {}).search_paths()


async def _open(
    paths: tuple[SearchPath, ...], filepath: str, bound: Bindings
) -> CaseBase:
    try:
        return await asyncio.to_thread(
            open_case_base,
            paths,
            filepath,
            _limits(),
            embedder=bound.embedder,
            cache=CASE_BASES,
        )
    except Retry as exc:
        raise _bad_request(exc) from exc


@router.post("/case-bases/query/{filepath:path}", dependencies=[GENERATION_LIMIT])
async def query_case_base(
    filepath: str, request: CaseQueryRequest, user: CurrentUser
) -> CaseQueryResponse:
    """Rank the cases of a case base by their similarity to an example."""
    bound = await bindings()
    paths = _paths(user)
    case_base = await _open(paths, filepath, bound)

    try:
        example = await asyncio.to_thread(
            parse_query,
            case_base,
            paths,
            _limits(),
            request.query,
            request.query_path,
            request.query_key,
        )
        hits = await rank(case_base, example, request.limit, _limits(), bound)
    except Retry as exc:
        raise _bad_request(exc) from exc

    return CaseQueryResponse(
        matches=[match(hit, graph=True) for hit in hits],
        query=to_jsonable_python(example.value) if case_base.graphs else None,
    )


@router.get("/case-bases/{filepath:path}")
async def get_case_base(filepath: str, user: CurrentUser) -> CaseBaseResponse:
    """A case base with the configuration inferred from its data."""
    case_base = await _open(_paths(user), filepath, await bindings())

    return CaseBaseResponse(
        path=case_base.path,
        count=len(case_base.cases),
        graphs=case_base.graphs,
        config=case_base.config.model_dump(mode="json"),
        query_schema=dict(case_base.query_schema),
    )
