"""Jq tool callable — filter JSON documents instead of reading them."""

import json
from dataclasses import dataclass
from typing import Annotated, override

from pydantic import Field, JsonValue

from ..converters import JSON_SUFFIXES, is_json
from ..humanize import pluralize
from ..subprocesses import jq_filter
from .base import (
    ToolOutput,
    ToolRetry,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .documents import DocumentFilePathArg
from .formatting import cap_lines, hint_suffix
from .sink import OutputPathArg, RedirectedOutput, RedirectingPathTool

__all__ = ["JqFilterArg", "JqResult", "JqTool"]

SHAPE_FILTER = (
    'def shape: if type == "object" '
    "then (to_entries | map({(.key): (.value | type)}) | add // {}) "
    'elif type == "array" '
    "then {array: length, element: (if length > 0 then (.[0] | shape) else null end)} "
    "else type end; shape"
)
"""The filter a call with no filter runs: the document's keys and their types.

The cheap first call, for the same reason ``query_table`` answers a missing
query with the columns and the row count.  A document's own filter cannot be
written without knowing its keys, and the only other way to learn them is `.`,
which returns the whole file — the outcome this tool exists to avoid.
"""

JqFilterArg = Annotated[
    str | None,
    Field(
        description=(
            "jq filter expression to run against the document, e.g. "
            "`.items | map(.name)`. Omit it to get the top-level keys and "
            "their types (for an array, its length and the shape of its first "
            "element), which is the cheap first call."
        ),
    ),
]


@dataclass(slots=True, frozen=True)
class JqResult:
    """The values a filter produced, one entry per output jq emitted."""

    file_path: str
    filter: str
    values: tuple[JsonValue, ...] = ()
    truncated: bool = False
    source_encoding: str | None = None


@dataclass(slots=True, frozen=True)
class JqTool(RedirectingPathTool[JqResult]):
    """Filter a JSON document with jq instead of reading it line by line."""

    max_formatted_chars: int = 50_000

    @override
    async def __call__(
        self,
        file_path: DocumentFilePathArg,
        filter: JqFilterArg = None,
        output_path: OutputPathArg = None,
    ) -> ToolOutput[JqResult | RedirectedOutput]:
        """Filter a JSON document with a jq expression.

        Prefer this over reading a JSON document: the filter runs over the
        whole file and returns only what it selects, where a line read spends
        the context on the records the question does not need.  Call it without
        a filter first to learn the top-level keys and their types.
        """
        _sp, _local, absolute = resolve_file_or_retry(self.resolved_paths, file_path)

        if not is_json(file_path):
            raise ToolRetry(
                f"'{file_path}' is not a JSON document. Filterable formats: "
                f"{', '.join(sorted(JSON_SUFFIXES))}. Read anything else with "
                f"read_document.{sidecar_hint(file_path)}"
            )

        decoded = read_text_or_retry(absolute, file_path)

        try:
            values = await jq_filter(filter or SHAPE_FILTER, decoded.text)
        except ValueError as exc:
            raise ToolRetry(str(exc)) from exc

        return await self.redirect(
            self._result(file_path, filter, values, decoded.source_encoding),
            output_path,
        )

    def _result(
        self,
        file_path: str,
        filter: str | None,
        values: list[JsonValue],
        source_encoding: str | None,
    ) -> ToolOutput[JqResult]:
        """Budget the outputs and render them one compact JSON value per line.

        The budget is applied before the result is built, so what a redirect
        stores is what the model would have been shown, and what it trims is
        whole values rather than a JSON document cut mid-token.
        """
        lines = [json.dumps(value, default=str) for value in values]
        body, dropped = cap_lines(lines, self.max_formatted_chars)
        kept = values[: len(values) - dropped]

        hints: list[str] = []
        if dropped:
            hints.append(
                f"{dropped} more {pluralize(dropped, 'value')} cut by the "
                "output budget, narrow the filter or pass an output_path"
            )
        if filter is None:
            hints.append("shape only, pass a filter to select values")

        return ToolOutput(
            data=JqResult(
                file_path=file_path,
                filter=filter or SHAPE_FILTER,
                values=tuple(kept),
                truncated=bool(dropped),
                source_encoding=source_encoding,
            ),
            formatted=(body or "(no values)") + hint_suffix(hints),
        )
