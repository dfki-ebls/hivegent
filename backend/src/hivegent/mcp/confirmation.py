"""User confirmation for MCP mutations, in the multi-round-trip idiom.

MCP 2026-07-28 (SEP-2577) removed server-initiated requests, so a tool can no
longer block on ``ctx.elicit`` and wait for an answer: FastMCP refuses the call
on a modern connection outright.  SEP-2322 replaces it with a round trip the
client drives.  The tool answers with an :class:`InputRequiredResult` carrying
the elicitation, the client collects the answer and retries the very same call
with it attached, and the tool runs again from the top with
``ctx.input_responses`` populated.

Only the transport changed, not the elicitation itself, so the schema and the
response are handled by the very functions ``ctx.elicit`` calls
(:func:`parse_elicit_response_type`, :func:`handle_elicit_accept`).  The
confirmation is therefore a plain ``bool``, and what comes back is validated
by Pydantic before it is believed.

The retry is a fresh call, so nothing about the first leg survives except what
the client hands back.  The caller therefore passes a digest of everything the
user is asked about, and the retry is checked against it: a client whose
arguments drifted between the two legs is asked again rather than quietly
writing something the user never saw.  It is not a defense against a hostile
client, which could answer its own elicitation.  The gate is for the person at
the other end of one, and whether a token may write at all is the auth layer's
question.  A client that ignores ``input_required`` never reaches the mutation,
so the gate fails closed.
"""

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import (
    handle_elicit_accept,
    parse_elicit_response_type,
)
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from pydantic import ValidationError

__all__ = ["confirm"]

_RESPONSE_KEY = "confirmation"
"""Server-assigned key the answer to our one elicitation comes back under."""

_CONFIRMATION = parse_elicit_response_type(
    bool,
    response_title="Allow",
    response_description="Apply this change to the workspace.",
)
"""Schema and validator for the answer, derived once from the response type."""


def _allowed(answer: ElicitResult) -> bool:
    """Validate the client's answer, refusing anything that is not a yes."""
    if answer.action != "accept":
        return False

    try:
        return handle_elicit_accept(_CONFIRMATION, answer.content).data is True
    except (ValidationError, ValueError) as exc:
        raise ToolError("The confirmation response was malformed.") from exc


def confirm(ctx: Context, message: str, digest: str) -> InputRequiredResult | None:
    """Return the ask to hand back, or ``None`` once the user allowed it.

    Args:
        ctx: The live request context, carrying any prior leg's answer.
        message: The question put to the user.
        digest: Fingerprint of everything *message* describes and the call
            would do, binding an answer to this change and no other.

    Returns:
        The result the tool must return to ask the client for confirmation,
        or ``None`` when the user has already allowed exactly this change.

    Raises:
        ToolError: The user refused, or answered with something unreadable.
    """
    answer = (ctx.input_responses or {}).get(_RESPONSE_KEY)

    if isinstance(answer, ElicitResult) and ctx.request_state == digest:
        if _allowed(answer):
            return None

        raise ToolError("The change was denied by the user.")

    return InputRequiredResult(
        input_requests={
            _RESPONSE_KEY: ElicitRequest(
                params=ElicitRequestFormParams(
                    message=message, requested_schema=_CONFIRMATION.schema
                )
            )
        },
        request_state=digest,
    )
