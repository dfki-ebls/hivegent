"""Jq tool callable — filter JSON documents instead of reading them."""

import json
from dataclasses import dataclass
from typing import Annotated, override

from pydantic import Field, JsonValue

from ..converters import JSON_SUFFIXES, is_json
from ..subprocesses import jq_filter
from .base import (
    AsyncPathTool,
    Batch,
    BatchShare,
    ToolOutput,
    ToolRetry,
    batch_field,
    read_text_or_retry,
    resolve_file_or_retry,
    run_batch,
    sidecar_hint,
)
from .formatting import hint_suffix

__all__ = ["JqFilePathsArg", "JqFilterArg", "JqResult", "JqTool"]

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

JqFilePathsArg = Annotated[
    list[str],
    batch_field(
        "Full workspace paths of the JSON documents to filter, each with the "
        "same filter."
    ),
]

JqFilterArg = Annotated[
    str | None,
    Field(
        description=(
            "jq filter expression to run against each document, e.g. "
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
    source_encoding: str | None = None


@dataclass(slots=True, frozen=True)
class JqTool(AsyncPathTool[Batch[JqResult]]):
    """Filter JSON documents with jq instead of reading them line by line."""

    @override
    async def __call__(
        self,
        file_paths: JqFilePathsArg,
        filter: JqFilterArg = None,
    ) -> ToolOutput[Batch[JqResult]]:
        """Filter one or more JSON documents with a jq expression.

        Prefer this over reading a JSON document: the filter runs over the
        whole file and returns only what it selects, where a line read spends
        the context on the records the question does not need.  Call it without
        a filter first to learn the top-level keys and their types.  Each
        document is filtered on its own and reported under its path.
        """

        async def run(file_path: str, _share: BatchShare) -> ToolOutput[JqResult]:
            return await self._filter(file_path, filter)

        return await run_batch(file_paths, run, key=lambda path: path)

    async def _filter(self, file_path: str, filter: str | None) -> ToolOutput[JqResult]:
        """Run the filter over one document."""
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

        body = "\n".join(json.dumps(value, default=str) for value in values)
        hints = ["shape only, pass a filter to select values"] if filter is None else []

        return ToolOutput(
            data=JqResult(
                file_path=file_path,
                filter=filter or SHAPE_FILTER,
                values=tuple(values),
                source_encoding=decoded.source_encoding,
            ),
            formatted=(body or "(no values)") + hint_suffix(hints),
        )
