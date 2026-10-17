"""Typed async wrapper around ``jq``."""

import os
import re

from pydantic import JsonValue

from .base import SubprocessError, run

__all__ = ["jq_filter"]

# A module directive is jq's only way to read a file, and an absolute
# ``search`` path reaches any one on disk.  Directives are bare top-level
# keywords, so a field (``.include``), a variable, or a string never matches.
_MODULE_DIRECTIVE = re.compile(r'(?<![\w.$"])(?:import|include)(?!\w)')


async def jq_filter(filter_expr: str, document: str) -> list[JsonValue]:
    """Run a jq filter against a JSON *document*.

    The document is passed as the text it was read as rather than as a parsed
    object: jq parses it either way, so decoding it here only to re-encode it
    would hold a large file in memory twice and move a malformed-JSON error out
    of jq's message and into a traceback of our own.

    The filter sees the document alone: module directives are refused, and jq
    runs without the server's environment, which ``$ENV`` would otherwise expose.

    Args:
        filter_expr: A jq filter expression (e.g. ``".[] | .name"``).
        document: JSON text to filter.

    Returns:
        List of parsed JSON values produced by the filter.

    Raises:
        ValueError: If the document or the jq expression is invalid, or
            execution fails.
    """
    if _MODULE_DIRECTIVE.search(filter_expr):
        raise ValueError("jq failed: import and include are not available")

    try:
        result = await run(
            ["jq", "-c", filter_expr],
            stdin=document.encode("utf-8"),
            env={"PATH": os.environ.get("PATH", "")},
        )
    except SubprocessError as exc:
        # jq exits 2 for usage errors, 3 for compile errors, 5 on bad input
        raise ValueError(f"jq failed: {exc.result.stderr_text.strip()}") from exc

    return list(result.stdout_ndjson())
