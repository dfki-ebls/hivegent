"""Jq tool callable — run jq filters against JSON files."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import jq_filter
from .base import Tool

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
class JqTool(Tool):
    """Run a jq filter against a JSON file."""

    path: Path

    @override
    async def __call__(
        self,
        filter: JqFilterArg,
        filename: JqFilenameArg,
    ) -> str:
        """Run a jq filter expression against a JSON file."""
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if not file_path.is_file():
            return f"Error: file '{filename}' not found."
        data = json.loads(file_path.read_text(encoding="utf-8"))

        try:
            result = await jq_filter(filter, data)
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(result, default=str)
