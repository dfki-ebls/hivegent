"""The tool ranking a case base by example, or showing how it compares its cases."""

import asyncio
from typing import Annotated

from exar.tools import effect
from exar.tools.batch import tool_return
from pydantic import Field, JsonValue
from pydantic_ai import ToolReturn

from ..base import SearchPath, ToolFunc
from .casebase import (
    _LIMITS,
    Bind,
    CaseBaseCache,
    Limits,
    _bindings,
    open_case_base,
)
from .query import parse_query, rank
from .render import (
    SimilarCasesResult,
    _render_configuration,
    _render_matches,
    match,
)

__all__ = [
    "SimilarCasesLimitArg",
    "SimilarCasesPathArg",
    "SimilarCasesQueryArg",
    "SimilarCasesQueryKeyArg",
    "SimilarCasesQueryPathArg",
    "similar_cases",
]

SimilarCasesPathArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path of the case base: a CSV, XLSX, Parquet, JSON, JSON Lines, "
            "or BPMN file, or a folder of JSON or BPMN files."
        )
    ),
]

SimilarCasesQueryArg = Annotated[
    dict[str, JsonValue] | None,
    Field(
        description=(
            "The example to rank by: a partial record of the case base's attributes, "
            "or for graphs an inline graph. Omit every query argument to get the "
            "attributes and how each is compared."
        ),
        # An empty query ranks nothing, and a strict schema that requires the argument would otherwise get `{}` for null.
        min_length=1,
    ),
]

SimilarCasesQueryPathArg = Annotated[
    str | None,
    Field(
        description="Full workspace path of a BPMN or cbrkit graph file to rank graphs by."
    ),
]

SimilarCasesQueryKeyArg = Annotated[
    str | None,
    Field(
        description="Key of a case of the case base to find the cases most similar to it."
    ),
]

SimilarCasesLimitArg = Annotated[
    int, Field(description="Number of cases to return.", ge=1, le=_LIMITS.max_limit)
]


def similar_cases(
    paths: tuple[SearchPath, ...],
    *,
    limits: Limits = _LIMITS,
    bind: Bind | None = None,
    cache: CaseBaseCache | None = None,
) -> ToolFunc:
    """The tool that ranks the cases of a case base below *paths* by their similarity to an example, writing nothing.

    Args:
        paths: The search paths case bases and query graphs are resolved against.
        limits: What one case base and one query may cost.
        bind: Gets the embedders and language models of the host.
        cache: Where loaded case bases are kept.
    """

    @effect("read")
    async def similar_cases(
        path: SimilarCasesPathArg,
        query: SimilarCasesQueryArg = None,
        query_path: SimilarCasesQueryPathArg = None,
        query_key: SimilarCasesQueryKeyArg = None,
        limit: SimilarCasesLimitArg = 5,
    ) -> ToolReturn[SimilarCasesResult]:
        """Find the most similar cases of a case base by example, with why each one matched.

        Use it to find past records like a new one (offers, tickets, machines,
        projects) where "about" and "similar" matter rather than exact values,
        or processes that contain a pattern.  Every case gets a similarity in
        [0, 1], broken down by queried attribute, or by the mapping of the
        query's nodes for graphs.  Call it with only the path first to learn
        the attributes, their values, and how each is compared.
        """
        bindings = await _bindings(bind)
        case_base = await asyncio.to_thread(
            open_case_base,
            paths,
            path,
            limits,
            embedder=bindings.embedder,
            cache=cache,
        )

        if query is None and query_path is None and query_key is None:
            return tool_return(
                SimilarCasesResult(
                    case_base.path,
                    len(case_base.cases),
                    case_base.graphs,
                    configuration=case_base.config.model_dump(mode="json"),
                ),
                _render_configuration(case_base, limits.max_limit),
            )

        example = await asyncio.to_thread(
            parse_query, case_base, paths, limits, query, query_path, query_key
        )
        hits = await rank(case_base, example, limit, limits, bindings)

        return tool_return(
            SimilarCasesResult(
                case_base.path,
                len(case_base.cases),
                case_base.graphs,
                tuple(match(hit) for hit in hits),
            ),
            _render_matches(case_base, hits, example.value),
        )

    return similar_cases
