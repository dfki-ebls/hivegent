"""Unit tests for the MCP mutation confirmation gate."""

from dataclasses import dataclass
from typing import Literal, cast

import pytest
from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ElicitResult, InputRequiredResult, InputResponses

from hivegent.mcp.confirmation import PendingMutation, confirm_mutation

MUTATION = PendingMutation(
    summary="replace of '~/notes.md'", payload=("hello",)
)

ElicitAction = Literal["accept", "decline", "cancel"]


@dataclass(slots=True, frozen=True)
class FakeContext:
    """The two fields the confirmation gate reads off a live request context."""

    input_responses: InputResponses | None = None
    request_state: str | None = None


def _confirm(context: FakeContext) -> InputRequiredResult | None:
    return confirm_mutation(cast(Context, cast(object, context)), MUTATION)


type ElicitContent = dict[str, str | int | float | bool | list[str] | None]


def _answered(
    action: ElicitAction,
    content: ElicitContent | None,
    state: str = MUTATION.digest,
) -> FakeContext:
    """A retry leg carrying the client's answer to our elicitation."""
    return FakeContext(
        input_responses={"confirmation": ElicitResult(action=action, content=content)},
        request_state=state,
    )


def test_first_call_asks_the_client_instead_of_mutating() -> None:
    ask = _confirm(FakeContext())

    assert isinstance(ask, InputRequiredResult)
    assert ask.request_state == MUTATION.digest
    assert ask.input_requests is not None
    rendered = str(ask.input_requests["confirmation"].model_dump())
    assert MUTATION.summary in rendered and "boolean" in rendered


def test_accepted_retry_lets_the_mutation_through() -> None:
    assert _confirm(_answered("accept", {"value": True})) is None


@pytest.mark.parametrize(
    ("action", "content"),
    [("accept", {"value": False}), ("decline", None), ("cancel", None)],
)
def test_refusal_stops_the_mutation(
    action: ElicitAction, content: ElicitContent | None
) -> None:
    with pytest.raises(ToolError, match="denied by the user"):
        _confirm(_answered(action, content))


def test_malformed_answer_is_refused_rather_than_believed() -> None:
    with pytest.raises(ToolError, match="malformed"):
        _confirm(_answered("accept", {"value": "yes please"}))


def test_answer_for_a_different_mutation_asks_again() -> None:
    # The retry is a fresh call, so an acceptance carried back against drifted
    # arguments must not stand in for one the user never saw.
    other = PendingMutation(summary=MUTATION.summary, payload=("goodbye",))
    ask = _confirm(_answered("accept", {"value": True}, state=other.digest))

    assert isinstance(ask, InputRequiredResult)
