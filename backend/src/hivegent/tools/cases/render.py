"""Rendering the ranked cases and the configuration of a case base as Markdown tables.

Both share the attribute columns of the records: the configuration lists
each attribute with its measure, weight and values, a ranking each case with
its values and, in parentheses, the similarity of every queried attribute.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cbrkit.helpers import format_number
from cbrkit.model import Explanation, Hit
from cbrkit.model.graph import Graph
from cbrkit.spec.pointer import get, parent, segments
from cbrkit.typing import Float
from exar.text import markdown_cell, markdown_table, truncate_line
from pydantic import JsonValue
from pydantic_core import to_json

from .casebase import CaseBase, Key

__all__ = [
    "CaseMatch",
    "SimilarCasesResult",
    "match",
]

_VALUE_CHARS = 80


type CaseMatch = Hit[Key, JsonValue, float]
"""A case of a ranking with its similarity as its score and why, its value as JSON, none for a graph unless asked for."""


@dataclass(slots=True, frozen=True)
class SimilarCasesResult:
    """The ranking of one call, or the configuration of a call without a query."""

    path: str
    count: int = 0
    graphs: bool = False
    matches: tuple[CaseMatch, ...] = ()
    configuration: JsonValue = None


def _cell(value: Any) -> str:
    """A value as a table cell, text as is and anything else as compact JSON.

    Examples:
        >>> _cell("a|b"), _cell(12808.8), _cell(None), _cell([1, 2])
        ('a\\\\|b', '12808.8', '', '[1,2]')
    """
    match value:
        case None:
            text = ""
        case bool():
            text = "true" if value else "false"
        case int() | float():
            text = format_number(value)
        case str():
            text = value
        case _:
            text = to_json(value, fallback=str).decode()

    return markdown_cell(truncate_line(text, _VALUE_CHARS))


def _label(pointer: str) -> str:
    """An attribute path joined by dots, any other pointer as is.

    Examples:
        >>> _label("/attributes/owner/attributes/city"), _label("/node/attributes/name"), _label("/edge")
        ('owner.city', 'node.name', '/edge')
    """
    if "/attributes/" not in pointer:
        return pointer

    return ".".join(segment for segment in segments(pointer) if segment != "attributes")


def _measure(measure: JsonValue) -> str:
    """A measure as its kind and its parameters, a nested measure by its kind.

    Examples:
        >>> _measure({"kind": "linear", "max_distance": 12808.8, "min_distance": 0.0, "cutoff": None})
        'linear max_distance=12808.8 min_distance=0'
    """
    if not isinstance(measure, dict):
        return ""

    params = [
        f"{name}={value['kind'] if isinstance(value, dict) and 'kind' in value else _cell(value)}"
        for name, value in measure.items()
        if name != "kind" and value is not None and value != {} and value != []
    ]

    return " ".join([str(measure.get("kind", "")), *params])


def _weight(tree: JsonValue, pointer: str) -> str:
    """The weight of the attribute at *pointer* in its attribute-value measure, empty for an element that is none."""
    match parent(pointer):
        case (head, name) if head.endswith("/attributes"):
            aggregator = (
                get(tree, f"{head.removesuffix('/attributes')}/aggregator") or {}
            )
            weights = aggregator.get("weights") or {}

            return _cell(weights.get(name, aggregator.get("default_weight", 1)))
        case _:
            return ""


def _enum(schema: Mapping[str, Any]) -> list[Any] | None:
    """The values a query schema lists for an attribute, None for an attribute it lists none of."""
    return next(
        (
            branch["enum"]
            for branch in [schema, *schema.get("anyOf", ())]
            if "enum" in branch
        ),
        None,
    )


def _values(schema: Mapping[str, Any]) -> str:
    """The values a query schema lists for an attribute, or the range of its examples."""
    if (enum := _enum(schema)) is not None:
        return ", ".join(map(_cell, enum))

    match schema.get("examples"):
        case [low, high]:
            return f"{_cell(low)} to {_cell(high)}"
        case _:
            return ""


def _configuration_rows(case_base: CaseBase, tree: JsonValue) -> Iterator[list[str]]:
    properties: Mapping[str, Mapping[str, Any]] = (
        {} if case_base.graphs else case_base.query_schema.get("properties", {})
    )

    for pointer, decision in case_base.config.provenance.items():
        measure = get(tree, pointer)

        if not pointer or (
            isinstance(measure, dict) and measure.get("kind") == "attribute_value"
        ):
            continue

        source = decision.source + (f" {decision.model}" if decision.model else "")
        absent = ", absent from the data" if decision.absent else ""
        yield [
            _label(pointer),
            _measure(measure),
            _weight(tree, pointer),
            _values(properties.get(_label(pointer), {})),
            markdown_cell(f"{source}: {decision.reason}{absent}"),
        ]


def _render_configuration(case_base: CaseBase, max_limit: int) -> str:
    config = case_base.config
    kind = "graphs" if case_base.graphs else "records"
    tree = config.model_dump(mode="json")["measure"]
    query = (
        "Query with query_path naming a BPMN or cbrkit graph file, with query as an "
        "inline graph {nodes: {key: value}, edges: {key: {source, target, value}}}, "
        "or with query_key naming one of its cases."
        if case_base.graphs
        else "Query with query as a partial record of these attributes, left out ones do "
        "not count, or with query_key naming one of its cases."
    )

    return "\n".join(
        [
            (
                f"{case_base.path}: {len(case_base.cases)} {kind} compared by {tree.get('kind')}, "
                f"at most {min(config.limits.max_limit, max_limit)} per query, configuration inferred from the data."
            ),
            markdown_table(
                [
                    "attribute" if not case_base.graphs else "element",
                    "measure",
                    "weight",
                    "values",
                    "decision",
                ],
                _configuration_rows(case_base, tree),
            ),
            query,
        ]
    )


def match(hit: Hit[Key, Any, Float], *, graph: bool = False) -> CaseMatch:
    """A ranked case as JSON, a graph case only with *graph*, since its key names it."""
    value: JsonValue = (
        None
        if isinstance(hit.value, Graph) and not graph
        else hit.model_dump(mode="json", include={"value"})["value"]
    )

    return Hit(
        key=hit.key, value=value, score=float(hit.score), explanation=hit.explanation
    )


def _graph_lines(explanation: Explanation | None) -> list[str]:
    graph = explanation.graph if explanation else None

    if graph is None:
        return []

    lines = [
        (
            f"  nodes {graph.node_coverage:.0%} and edges {graph.edge_coverage:.0%} "
            f"of the query mapped{'' if graph.exact else ', approximately'}"
        ),
        *(
            f"  node {node.query} -> {node.case}: {node.similarity:.2f}"
            for node in graph.nodes
        ),
    ]

    if graph.unmapped_nodes:
        lines.append(f"  unmapped nodes: {', '.join(map(str, graph.unmapped_nodes))}")

    return lines


def _render_graphs(hits: Sequence[Hit[Key, Any, Float]]) -> str:
    return "\n\n".join(
        "\n".join(
            [
                f"[{index}] {hit.key}: {float(hit.score):.2f}",
                *_graph_lines(hit.explanation),
            ]
        )
        for index, hit in enumerate(hits, 1)
    )


def _rank(index: int, hit: Hit[Key, Any, Float]) -> list[str]:
    return [str(index), _cell(hit.key), f"{float(hit.score):.2f}"]


def _render_records(hits: Sequence[Hit[Key, Any, Float]], query: Any) -> str:
    if not all(isinstance(hit.value, Mapping) for hit in hits):
        return markdown_table(
            ["#", "key", "score", "case"],
            (
                [*_rank(index, hit), _cell(hit.value)]
                for index, hit in enumerate(hits, 1)
            ),
        )

    queried = list(query) if isinstance(query, Mapping) else []
    columns = list(
        dict.fromkeys([*queried, *(name for hit in hits for name in hit.value)])
    )
    rows = (
        [
            [
                "",
                "query",
                "",
                *(
                    _cell(query.get(name)) if name in queried else ""
                    for name in columns
                ),
            ]
        ]
        if queried
        else []
    )

    for index, hit in enumerate(hits, 1):
        attributes = hit.explanation.attributes if hit.explanation else {}
        cells = [
            f"{_cell(hit.value.get(name))} ({attributes[name].similarity:.2f})"
            if name in attributes and name in queried
            else _cell(hit.value.get(name))
            for name in columns
        ]
        rows.append([*_rank(index, hit), *cells])

    return markdown_table(["#", "key", "score", *columns], rows)


def _folded(value: str) -> str:
    return " ".join(value.split()).casefold()


def _query_notes(case_base: CaseBase, query: Any) -> list[str]:
    """Why a query attribute matches no case well: a category the records lack, or a number beyond their range.

    Scores alone cannot tell such a query from one whose cases are merely far away.
    """
    if not isinstance(query, Mapping):
        return []

    properties: Mapping[str, Mapping[str, Any]] = case_base.query_schema.get(
        "properties", {}
    )
    notes: list[str] = []

    for name, value in query.items():
        schema = properties.get(name, {})
        enum = _enum(schema)

        if (
            isinstance(value, str)
            and enum is not None
            and _folded(value) not in {_folded(str(item)) for item in enum}
        ):
            notes.append(
                f"{name} {_cell(value)} is none of its values ({_values(schema)}), so no case matches it."
            )

        match schema.get("examples"):
            case [int() | float() as low, int() | float() as high] if (
                isinstance(value, int | float) and not low <= value <= high
            ):
                bound = (
                    f"below the smallest value {_cell(low)}"
                    if value < low
                    else f"above the largest value {_cell(high)}"
                )
                notes.append(f"{name} {_cell(value)} is {bound} of the records.")
            case _:
                pass

    return notes


def _render_matches(
    case_base: CaseBase, hits: Sequence[Hit[Key, Any, Float]], query: Any
) -> str:
    if not hits:
        return "(no similar cases)"

    if all(float(hit.score) == 0 for hit in hits):
        ranking = "No case shares anything with the query, every one scores 0, so no ranking is shown."
    elif case_base.graphs:
        ranking = _render_graphs(hits)
    else:
        ranking = _render_records(hits, query)

    return "\n".join(
        [ranking, *(f"Note: {note}" for note in _query_notes(case_base, query))]
    )
