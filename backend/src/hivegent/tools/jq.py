"""Jq tool callable — run jq filters against JSON files."""

import json
from dataclasses import dataclass
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import jq_filter
from .base import (
    AsyncPathTool,
    ToolOutput,
    ToolRetry,
    read_text_or_retry,
    resolve_accessible_file,
)
from .documents import DocumentFilePathArg

__all__ = ["JqFilterArg", "JqTool"]

JqFilterArg = Annotated[
    str,
    Field(description="jq filter expression to apply."),
]


@dataclass(slots=True, frozen=True)
class JqTool(AsyncPathTool[str]):
    """Run a jq filter against a JSON file."""

    max_output_chars: int = 100_000

    @override
    async def __call__(
        self,
        filter: JqFilterArg,
        file_path: DocumentFilePathArg,
    ) -> ToolOutput[str]:
        """Run a jq filter expression against a JSON file."""
        resolved = resolve_accessible_file(self.resolved_paths, file_path)
        if resolved is None or not resolved[2].is_file():
            raise ToolRetry(f"file '{file_path}' not found.")
        data = json.loads(read_text_or_retry(resolved[2], file_path).text)

        try:
            result = await jq_filter(filter, data)
        except ValueError as exc:
            raise ToolRetry(str(exc)) from exc
        output = json.dumps(result, default=str)
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + "\n\n[truncated]"
        return ToolOutput(data=output)
