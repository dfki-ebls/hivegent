"""Typed async wrapper around ``jq``."""

from pydantic import JsonValue

from .base import SubprocessError, run

__all__ = ["jq_filter"]


async def jq_filter(filter_expr: str, document: str) -> list[JsonValue]:
    """Run a jq filter against a JSON *document*.

    The document is passed as the text it was read as rather than as a parsed
    object: jq parses it either way, so decoding it here only to re-encode it
    would hold a large file in memory twice and move a malformed-JSON error out
    of jq's message and into a traceback of our own.

    Args:
        filter_expr: A jq filter expression (e.g. ``".[] | .name"``).
        document: JSON text to filter.

    Returns:
        List of parsed JSON values produced by the filter.

    Raises:
        ValueError: If the document or the jq expression is invalid, or
            execution fails.
    """
    try:
        result = await run(["jq", "-c", filter_expr], stdin=document.encode("utf-8"))
    except SubprocessError as exc:
        # jq exits 2 for usage errors, 3 for compile errors, 5 on bad input
        raise ValueError(f"jq failed: {exc.result.stderr_text.strip()}") from exc

    return list(result.stdout_ndjson())
