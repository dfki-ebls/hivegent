"""The chat debugger's conversation steps and report leak check."""

from datetime import UTC, datetime, timedelta

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    RetryInfo,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents.debugger import LeakCheck, conversation_steps, write_report
from hivegent.db.conversations import CHAT_ERROR_KEY

_START = datetime(2026, 1, 1, tzinfo=UTC)


def test_conversation_steps() -> None:
    """Calls, failures, and run errors become timed steps."""
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[UserPromptPart("Find the invoice")],
            instructions="Be helpful.",
            timestamp=_START,
        ),
        ModelResponse(
            parts=[ToolCallPart("search", {"query": "invoice"}, tool_call_id="a")],
            timestamp=_START + timedelta(seconds=1),
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "search",
                    "boom",
                    tool_call_id="a",
                    outcome="failed",
                    timestamp=_START + timedelta(seconds=4),
                ),
                RetryFeedbackPart("bad query", retry=RetryInfo(cause="model_retry")),
            ],
            instructions="Be helpful.",
        ),
        ModelResponse(parts=[TextPart("Sorry.")], metadata={CHAT_ERROR_KEY: "oops"}),
    ]

    steps = list(conversation_steps(messages))

    assert [step.kind for step in steps] == [
        "instructions",
        "user",
        "model",
        "call",
        "return",
        "retry",
        "model",
        "assistant",
        "error",
    ]
    assert [step.index for step in steps if step.failed] == [4, 5, 8]
    assert steps[2].took == timedelta(seconds=1)
    assert steps[4].took == timedelta(seconds=3)


_CONVERSATION = [
    "Frau Müller from Kaiserslautern asked about invoice 4711 and kubectl yesterday morning.",
]


def test_leak_check() -> None:
    """Names, numbers, and rare words leak, placeholders and common or own words do not."""
    check = LeakCheck.of(_CONVERSATION, own=["search_documents"])
    report = (
        "Müller asked about <DOCUMENT_1> at #4711, search_documents failed. "
        "She asked about invoice 4711 and kubectl yesterday morning."
    )

    assert check.leaks(report) == ["Müller", "4711", "kubectl"]
    assert (
        LeakCheck.mask("Ask Müller about it.", ["Müller"])
        == "Ask <REDACTED_1> about it."
    )


async def test_report_masks_after_retry() -> None:
    """A leak the retry keeps is masked."""
    replies = iter(["Müller failed.", "Herr Müller failed."])

    def reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(next(replies))])

    report = await write_report(
        "session",
        LeakCheck.of(_CONVERSATION, own=[]),
        FunctionModel(reply),
        {},
    )

    assert report.markdown == "Herr <REDACTED_1> failed."
    assert report.masked == 1
