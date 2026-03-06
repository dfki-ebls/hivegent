"""Typed async wrapper around ``jq``."""

import json
from typing import Any

from .base import SubprocessError, run

__all__ = ["jq_filter"]


async def jq_filter(
    filter_expr: str,
    data: Any,
    *,
    raw_output: bool = False,
) -> list[Any]:
    """Run a jq filter against *data*.

    Args:
        filter_expr: A jq filter expression (e.g. ``".[] | .name"``).
        data: Python object to serialize as JSON input.
        raw_output: If ``True``, pass ``-r`` so jq emits raw strings
            instead of JSON-encoded strings.

    Returns:
        List of parsed JSON values produced by the filter.

    Raises:
        ValueError: If the jq expression is invalid or execution fails.
    """
    args = ["jq", "-c"]
    if raw_output:
        args.append("-r")
    args.append(filter_expr)

    stdin = json.dumps(data, default=str).encode("utf-8")
    try:
        result = await run(args, stdin=stdin)
    except SubprocessError as exc:
        # jq exits 2 for usage errors, 3 for compile errors
        raise ValueError(f"jq failed: {exc.result.stderr_text.strip()}") from exc

    return list(result.stdout_ndjson())
