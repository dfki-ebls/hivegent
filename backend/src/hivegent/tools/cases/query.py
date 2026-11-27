"""Parsing a query by example and ranking a case base against it with cbrkit."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from cbrkit.errors import CbrkitError, Unbound
from cbrkit.indexable import InMemory
from cbrkit.io import malformed
from cbrkit.model import Hit
from cbrkit.model.graph import Graph, from_bpmn, from_dict, from_json, parse_bpmn
from cbrkit.sim.embed import EmbeddingCache
from cbrkit.spec import case_model
from cbrkit.system import System
from cbrkit.typing import Float
from exar.tools import Retry
from exar.tools.common import read_bytes_or_retry
from pydantic import JsonValue, ValidationError

from ..base import SearchPath, resolve_file_or_retry
from .casebase import (
    DECISION_MODEL,
    EMBEDDER,
    LANGUAGE_MODEL,
    Bindings,
    CaseBase,
    Key,
    Limits,
)

__all__ = [
    "Query",
    "parse_query",
    "rank",
]


def _graph_file(
    paths: tuple[SearchPath, ...], path: str, max_bytes: int
) -> Graph[Any, Any, Any, Any]:
    """The graph of the BPMN or cbrkit graph file *path* of at most *max_bytes*, following no symlink.  Blocking."""
    sp, local, absolute = resolve_file_or_retry(paths, path)

    def check(size: int) -> None:
        if size > max_bytes:
            raise Retry(
                f"'{path}' is larger than {max_bytes} bytes, more than a query may load."
            )

    data = read_bytes_or_retry(sp.mount, local, path, check)

    try:
        with malformed(path):
            return (
                from_bpmn(parse_bpmn(data))
                if absolute.suffix.lower() == ".bpmn"
                else from_json(data)
            )
    except (CbrkitError, KeyError, TypeError) as exc:
        raise Retry(f"'{path}' is no BPMN or cbrkit graph file: {exc}") from exc


def _stored_columns(case_base: CaseBase) -> set[str]:
    """The attributes of the stored records of *case_base*, measured or not."""
    return {
        name
        for case in case_base.cases.values()
        if isinstance(case, Mapping)
        for name in case
    }


def _blank(value: JsonValue) -> bool:
    """Whether a value of a query attribute is missing, null or empty text alike.

    >>> _blank(None), _blank(" "), _blank(0)
    (True, True, False)
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _examples(
    case_base: CaseBase,
    values: Sequence[Mapping[str, JsonValue]],
    role: Literal["query", "solution"],
) -> list[Any]:
    """*values* as cases of *case_base*, graphs or partial records.

    A query names only measured attributes, an empty one counting as left
    out, while a solution may also fill the attributes the stored records
    hold without a measure.

    Raises:
        Retry: When a value does not fit the case base.
    """
    try:
        if case_base.graphs:
            return [from_dict(value) for value in values]

        solution = role == "solution"
        model = case_model(case_base.config, stored=solution)
        known = set(model.model_fields) | (
            _stored_columns(case_base) if solution else set()
        )
        fitted: list[Any] = []

        for value in values:
            if not solution:
                value = {name: item for name, item in value.items() if not _blank(item)}

            if unknown := sorted(set(value) - known):
                raise KeyError(f"unknown attributes {', '.join(unknown)}")

            fitted.append(model.model_validate(value).model_dump(exclude_unset=True))

        return fitted
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        details = (
            exc.errors(include_url=False)[:3]
            if isinstance(exc, ValidationError)
            else exc
        )
        raise Retry(
            f"The {role} does not fit '{case_base.path}': {details}. "
            "Call similar_cases with only its path for its attributes."
        ) from exc


@dataclass(slots=True, frozen=True)
class Query:
    """The query of a call, a stored case if it has a key, which its ranking leaves out."""

    value: Any
    key: Key | None = None


def parse_query(
    case_base: CaseBase,
    paths: tuple[SearchPath, ...],
    limits: Limits,
    query: Mapping[str, JsonValue] | None = None,
    query_path: str | None = None,
    query_key: str | None = None,
) -> Query:
    """The query of a call: a partial record, a graph, or a case of the case base.  Blocking.

    Raises:
        Retry: When the query does not fit the case base.
    """
    if sum(arg is not None for arg in (query, query_path, query_key)) != 1:
        raise Retry("Pass exactly one of query, query_path, and query_key.")

    if query_key is not None:
        key = next((key for key in case_base.cases if str(key) == query_key), None)

        if key is None:
            raise Retry(f"'{case_base.path}' has no case '{query_key}'.")

        return Query(case_base.cases[key], key)

    if query_path is not None:
        if not case_base.graphs:
            raise Retry("query_path names a graph file, pass a record as query.")

        return Query(_graph_file(paths, query_path, limits.max_bytes))

    example = _examples(case_base, [query or {}], "query")[0]

    if not case_base.graphs and not example:
        raise Retry(
            "The query names no attribute with a value, so every case would be as similar as any other. "
            "Pass at least one attribute, call similar_cases with only the path for them."
        )

    return Query(example)


def _unbound(path: str, exc: Unbound) -> str:
    return (
        f"The similarity configuration of '{path}' cannot be used: {exc}. Hivegent "
        f"binds the embedder '{EMBEDDER}', with a chat model configured the "
        f"language model '{LANGUAGE_MODEL}', and with a decision model configured "
        f"the model '{DECISION_MODEL}', which fills decisions such as the measure 'fit'."
    )


def _system(case_base: CaseBase, limits: Limits, bindings: Bindings) -> System[Any]:
    """A system over the loaded cases, the limits of its configuration held to *limits*.

    Its embedders keep the vectors of the cases with the case base, so they are embedded once.
    """
    own = case_base.config.limits
    bounded = own.model_copy(
        update={
            "max_limit": min(own.max_limit, limits.max_limit),
            "max_node_pairs": min(
                own.max_node_pairs or limits.max_node_pairs, limits.max_node_pairs
            ),
        }
    )

    try:
        return System(
            config=case_base.config.model_copy(update={"limits": bounded}),
            storage=InMemory(case_base.cases),
            embedders={
                name: EmbeddingCache(embedder=embed, store=case_base.embeddings)
                for name, embed in bindings.embedders.items()
            },
            models=bindings.models,
        )
    except Unbound as exc:
        raise Retry(_unbound(case_base.path, exc)) from exc


async def rank(
    case_base: CaseBase,
    query: Query,
    limit: int,
    limits: Limits,
    bindings: Bindings | None = None,
) -> list[Hit[Key, Any, Float]]:
    """The cases most similar to *query*, each with its explanation.

    Raises:
        Retry: When the query costs more than *limits* allow or cannot be ranked.
    """
    system = _system(case_base, limits, bindings or Bindings())

    try:
        outcome = await system.retrieve(
            None if query.key is not None else query.value,
            key=query.key,
            limit=limit,
            explain=True,
        )
    except (CbrkitError, ValueError, KeyError, TypeError) as exc:
        raise Retry(f"'{case_base.path}' cannot rank the query: {exc}") from exc

    return list(outcome.hits)
