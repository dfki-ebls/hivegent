"""Jq tool callable — run jq filters against JSON files."""

import json
from dataclasses import dataclass
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import jq_filter
from .base import PathsTool, resolve_search_path

__all__ = ["JqFilenameArg", "JqFilterArg", "JqTool"]

JqFilterArg = Annotated[
    str,
    Field(description="jq filter expression to apply."),
]
JqFilenameArg = Annotated[
    str,
    Field(description="Relative JSON file path within the tool workspace."),
]


@dataclass(slots=True, frozen=True)
class JqTool(PathsTool):
    """Run a jq filter against a JSON file."""

    max_output_chars: int = 100_000

    @override
    async def __call__(
        self,
        filter: JqFilterArg,
        filename: JqFilenameArg,
    ) -> str:
        """Run a jq filter expression against a JSON file."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return f"Error: file '{filename}' not found."
        sp, local = resolved
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return "Error: path traversal detected."
        if not file_path.is_file():
            return f"Error: file '{filename}' not found."
        data = json.loads(file_path.read_text(encoding="utf-8"))

        try:
            result = await jq_filter(filter, data)
        except ValueError as exc:
            return f"Error: {exc}"
        output = json.dumps(result, default=str)
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + "\n\n[truncated]"
        return output
