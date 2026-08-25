"""User confirmation for MCP mutations, in the multi-round-trip idiom.

MCP 2026-07-28 (SEP-2577) removed server-initiated requests, so a tool can no
longer block on ``ctx.elicit`` and wait for an answer: FastMCP refuses the call
on a modern connection outright.  SEP-2322 replaces it with a round trip the
client drives — the tool answers with an :class:`InputRequiredResult` carrying
the elicitation, the client collects the answer and retries the very same call
with it attached, and the tool runs again from the top with
``ctx.input_responses`` populated.

Only the transport changed, not the elicitation itself, so the schema and the
response are handled by the very functions ``ctx.elicit`` calls
(:func:`parse_elicit_response_type`, :func:`handle_elicit_accept`) rather than
by a hand-written JSON Schema and a hand-read response dict.  The confirmation
is therefore a plain ``bool``, which is the response type upstream documents
for exactly this case, and what comes back is validated by Pydantic before it
is believed.

The retry is a fresh call, so nothing about the first leg survives except what
the client hands back.  That is why :class:`PendingMutation` renders the
prompt and the digest from the same fields, and the retry is checked against
that digest: a client whose arguments drifted between the two legs is asked
again rather than quietly writing something the user never saw.  It is not a
defense against a hostile client, which could answer its own elicitation — the
gate is for the person at the other end of one, and whether a token may write
at all is the auth layer's question, not this one's.

A client that ignores ``input_required`` never reaches the mutation, so the
gate fails closed.  The mutating tools also carry :data:`MUTATION_ANNOTATIONS`,
which is what drives the host's own approval prompt; the two are
complementary, since an annotation is a hint the client may ignore and this is
not.
"""

from dataclasses import dataclass

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
    ToolAnnotations,
)
from pydantic import ValidationError

from ..config import content_hash

__all__ = ["MUTATION_ANNOTATIONS", "PendingMutation", "confirm_mutation"]

MUTATION_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, open_world_hint=False
)
"""Behavioral hints every mutating MCP tool declares, driving the host's prompt.

Idempotence is left unstated: a replace is idempotent and an append is not, and
one shared annotation cannot claim either.
"""

_RESPONSE_KEY = "confirmation"
"""Server-assigned key the answer to our one elicitation comes back under."""

_CONFIRMATION = parse_elicit_response_type(
    bool,
    response_title="Allow",
    response_description="Apply this change to the workspace.",
)
"""Schema and validator for the answer, derived once from the response type."""


@dataclass(slots=True, frozen=True)
class PendingMutation:
    """One workspace mutation awaiting the user's answer.

    Both halves of a confirmation come off the same value — the sentence the
    user reads and the digest the retry is checked against — so an argument
    cannot be described to the user and left out of the fingerprint, or the
    reverse.

    Attributes:
        summary: The change as the user sees it, e.g. ``"edit to '~/a.md'"``.
        payload: Every argument that decides what lands on disk. Arguments
            that only guard the write (an ``expected_hash``) belong to the
            mutation gateway, not here.
    """

    summary: str
    payload: tuple[str, ...]

    @property
    def message(self) -> str:
        """The question put to the user."""
        return f"Allow the {self.summary}?"

    @property
    def digest(self) -> str:
        """Fingerprint binding an answer to this mutation and no other."""
        return content_hash("\0".join((self.summary, *self.payload)))


def _allowed(answer: ElicitResult) -> bool:
    """Validate the client's answer, refusing anything that is not a yes."""
    if answer.action != "accept":
        return False

    try:
        return handle_elicit_accept(_CONFIRMATION, answer.content).data is True
    except (ValidationError, ValueError) as exc:
        raise ToolError("The confirmation response was malformed.") from exc


def confirm_mutation(
    ctx: Context, mutation: PendingMutation
) -> InputRequiredResult | None:
    """Return the ask to hand back, or ``None`` once the user allowed it.

    Args:
        ctx: The live request context, carrying any prior leg's answer.
        mutation: The change being confirmed.

    Returns:
        The result the tool must return to ask the client for confirmation,
        or ``None`` when the user has already allowed exactly this mutation.

    Raises:
        ToolError: The user refused, or answered with something unreadable.
    """
    answer = (ctx.input_responses or {}).get(_RESPONSE_KEY)

    if isinstance(answer, ElicitResult) and ctx.request_state == mutation.digest:
        if _allowed(answer):
            return None

        raise ToolError(f"The {mutation.summary} was denied by the user.")

    return InputRequiredResult(
        input_requests={
            _RESPONSE_KEY: ElicitRequest(
                params=ElicitRequestFormParams(
                    message=mutation.message,
                    requested_schema=_CONFIRMATION.schema,
                )
            )
        },
        request_state=mutation.digest,
    )
