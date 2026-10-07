"""Tests for the cross-cutting run-loop safeguards."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import BinaryContent, FunctionToolset, ImageUrl, RunContext
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability
from pydantic_ai.exceptions import IncompleteToolCall
from pydantic_ai.messages import (
    FinishReason,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from hivegent.agents.app import title_agent
from hivegent.agents.approval import (
    APPROVAL_NOTE_KEY,
    ApprovalNotes,
    approval_note_text,
)
from hivegent.agents.common import UserDeps
from hivegent.agents.guards import (
    IncompleteToolCallGuard,
    IterationLimitWarner,
    PromptImageLimit,
    ToolOutputSpill,
    _images,
)
from hivegent.config import settings
from hivegent.l10n import current_language, use_language
from hivegent.store import Casebase
from hivegent.tmp import tmp_dir
from hivegent.tools.base import ToolOutput
from hivegent.tools.pydantic_ai import wrap_tool_output

_TRUNCATED_CALL = ToolCallPart(
    tool_name="edit_document", args='{"file_path":', tool_call_id="call-1"
)


def _run_context(
    requests: int = 0, limits: UsageLimits | None = None
) -> RunContext[None]:
    """A run context with no deps, as ``title_agent`` hands its guards."""
    return RunContext(
        deps=None,
        model=TestModel(),
        usage=RunUsage(requests=requests),
        usage_limits=limits,
    )


async def _check(
    parts: list[ModelResponsePart],
    finish_reason: FinishReason,
    settings: ModelSettings | None = None,
) -> None:
    """Run the guard over a response with *parts*, as the agent loop would."""
    await IncompleteToolCallGuard().after_model_request(
        _run_context(),
        request_context=ModelRequestContext(
            model=TestModel(),
            messages=[],
            model_settings=settings,
            model_request_parameters=ModelRequestParameters(),
        ),
        response=ModelResponse(parts=parts, finish_reason=finish_reason),
    )


async def test_tool_call_cut_off_by_the_token_limit_fails_the_turn() -> None:
    """Incomplete arguments must not reach the tool or the retry loop.

    Run through a real agent rather than the hook alone, so the guard riding
    on the agents themselves is what the assertion rests on: the tool named
    here is not even registered, and the turn still fails before validation
    would look for it.
    """

    def truncated(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[_TRUNCATED_CALL], finish_reason="length")

    with pytest.raises(IncompleteToolCall, match="provider default"):
        await title_agent.run("go", model=FunctionModel(truncated))


async def test_tool_calls_run_in_english_whatever_the_request_language() -> None:
    toolset = FunctionToolset[None]()
    toolset.add_function(current_language, name="probe")

    def call_probe(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="probe")])

        return ModelResponse(parts=[TextPart(content="done")])

    with use_language("de"):
        result = await title_agent.run(
            "go", model=FunctionModel(call_probe), toolsets=[toolset]
        )

    returns = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [part.content for part in returns] == ["en"]


async def test_the_message_names_the_limit_that_was_hit() -> None:
    with pytest.raises(IncompleteToolCall, match="1024"):
        await _check([_TRUNCATED_CALL], "length", ModelSettings(max_tokens=1024))


@pytest.mark.parametrize(
    ("parts", "finish_reason"),
    [
        # A finished call: the guard reads the finish reason, not the arguments.
        ([_TRUNCATED_CALL], "stop"),
        # Truncated prose is a degraded but usable answer, so it passes.
        ([TextPart(content="half a sen")], "length"),
    ],
)
async def test_anything_else_passes_through(
    parts: list[ModelResponsePart], finish_reason: FinishReason
) -> None:
    await _check(parts, finish_reason)


def _image() -> BinaryContent:
    return BinaryContent(data=b"\x89PNG", media_type="image/png")


def _read(tool_call_id: str) -> ToolReturnPart:
    """A binary read's return, shaped as pydantic-ai transcribes one."""
    return ToolReturnPart(
        tool_name="read_binary_document",
        content=["here it is", _image()],
        tool_call_id=tool_call_id,
    )


async def _sent(
    capability: AbstractCapability[Any],
    messages: list[ModelMessage],
    *,
    requests: int = 0,
    limits: UsageLimits | None = None,
) -> list[ModelMessage]:
    """The messages the guard puts on the wire for a request carrying *messages*."""
    sent: list[ModelMessage] = []

    async def handler(request_context: ModelRequestContext) -> ModelResponse:
        sent.extend(request_context.messages)

        return ModelResponse(parts=[TextPart(content="ok")])

    await capability.wrap_model_request(
        _run_context(requests, limits),
        request_context=ModelRequestContext(
            model=TestModel(),
            messages=messages,
            model_settings=None,
            model_request_parameters=ModelRequestParameters(),
        ),
        handler=handler,
    )

    return sent


async def test_the_whole_request_shares_one_image_allowance() -> None:
    """What the gateway counts is the request, so that is what is counted.

    The turn's own attachment and the two parallel reads of one step answer
    to a single number, which no per-call budget could hold them to, and the
    newest are the ones that survive.
    """
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content=["look at this", _image()])]),
        ModelRequest(parts=[_read("a"), _read("b")]),
    ]

    sent = await _sent(PromptImageLimit(2), messages)

    assert len(_images(sent)) == 2
    prompt = sent[0].parts[0]
    assert isinstance(prompt, UserPromptPart)
    assert isinstance(prompt.content, list)
    assert prompt.content[0] == "look at this"
    assert "image not shown" in str(prompt.content[1])


async def test_the_trim_is_spent_on_the_wire_and_not_on_the_conversation() -> None:
    """The run's own messages are the tree the conversation is replayed from.

    Both halves matter: the parts are shared with it, so an in-place swap
    would strip the image from the stored conversation, and the list is its
    history, so handing back a rebuilt one would rewrite it wholesale.
    """
    part = _read("a")
    messages: list[ModelMessage] = [ModelRequest(parts=[part, _read("b")])]

    sent = await _sent(PromptImageLimit(1), messages)

    assert len(_images(sent)) == 1
    assert len(_images(messages)) == 2
    assert isinstance(part.content, list)
    assert isinstance(part.content[1], BinaryContent)


@pytest.mark.parametrize("max_images", [None, 2])
async def test_a_request_the_gateway_accepts_is_passed_through(
    max_images: int | None,
) -> None:
    """No cap, or nothing over it, and the request is handed on untouched."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content=["look", _image(), _image()])])
    ]

    assert await _sent(PromptImageLimit(max_images), messages) == messages


@pytest.mark.parametrize("shape", ["scalar", "tuple", "url", "shared"])
async def test_image_occurrences_match_provider_content_shapes(shape: str) -> None:
    """Keep the newest occurrence across scalar tools and sequence prompts."""
    newest = _image()
    older = newest if shape == "shared" else _image()
    if shape == "scalar":
        part = ToolReturnPart(tool_name="image", content=older, tool_call_id="a")
    elif shape == "tuple":
        part = UserPromptPart(content=(older,))
    elif shape == "url":
        part = UserPromptPart(content=[ImageUrl(url="https://example.com/image.png")])
    else:
        part = UserPromptPart(content=[older])

    messages: list[ModelMessage] = [
        ModelRequest(parts=[part, UserPromptPart(content=[newest])])
    ]

    sent = await _sent(PromptImageLimit(1), messages)
    request = sent[0]
    assert isinstance(request, ModelRequest)
    trimmed = request.parts[0]
    assert isinstance(trimmed, UserPromptPart | ToolReturnPart)
    assert "image not shown" in str(trimmed.content)
    assert request.parts[1] is messages[0].parts[1]
    assert len(_images(messages)) == 2


async def test_the_wrap_up_note_stays_off_the_conversation() -> None:
    """A note appended to the run's messages would be recorded as a user turn.

    It would then replay on every later turn and gain one more note per
    warned request, so the nudge has to ride the wire alone.
    """
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="go")])]
    sent = await _sent(
        IterationLimitWarner(), messages, requests=3, limits=UsageLimits(request_limit=4)
    )

    assert "used 3 of 4 model requests" in str(sent[-1].parts[0])
    assert len(sent) == 2
    assert len(messages) == 1


async def test_the_wrap_up_note_reads_the_run_limits() -> None:
    """The run's own limit decides, so a run without one is never warned."""
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="go")])]

    assert await _sent(IterationLimitWarner(), messages, requests=40) == messages
    assert await _sent(
        IterationLimitWarner(), messages, requests=40, limits=UsageLimits(request_limit=100)
    ) == messages


@dataclass(slots=True, frozen=True)
class _Row:
    text: str
    truncated: bool = False


async def _spill(
    result: Any,
    *,
    conversation: str | None = "c1",
    call_id: str = "call-1",
    capability: AbstractCapability[UserDeps] | None = None,
    note: str | None = None,
) -> Any:
    """Bound *result* to 1000 characters as a grep call's return."""
    deps = UserDeps(
        user_id="u",
        store=Casebase.for_user("u"),
        mode="read",
        conversation_id=conversation,
    )
    capability = capability or ToolOutputSpill(max_chars=1_000)

    return await capability.after_tool_execute(
        RunContext(
            deps=deps,
            model=TestModel(),
            usage=RunUsage(),
            tool_call_metadata={APPROVAL_NOTE_KEY: note} if note else None,
        ),
        call=ToolCallPart(tool_name="grep", args={}, tool_call_id=call_id),
        tool_def=ToolDefinition(name="grep"),
        args={},
        result=result,
    )


def _saved(preview: str, data_dir: Path) -> Path:
    """The file the note in *preview* names, on disk."""
    found = re.search(r"`/tmp/(\.tool-results/grep-\w+\.\w+)`", preview)
    assert found is not None

    return tmp_dir(data_dir, "c1") / found[1]


async def test_a_structured_result_too_large_to_show_is_saved_as_json(
    data_dir: Path,
) -> None:
    rows = [_Row(f"row {i}", truncated=i == 0) for i in range(300)]
    text = "\n".join(row.text for row in rows)
    result = wrap_tool_output(ToolOutput(data=rows, formatted=text), tool_call_id="call-1")

    spilled = await _spill(result)

    assert isinstance(spilled, ToolReturn)
    assert spilled.metadata is result.metadata
    preview = spilled.return_value
    assert isinstance(preview, str)
    assert len(preview) <= 1_000
    assert preview.startswith("row 0\nrow 1\n")
    assert preview.endswith("\nrow 299")
    assert "300 entries" in preview
    assert "already cut it short" in preview
    saved = _saved(preview, data_dir)
    assert saved.suffix == ".json"
    assert len(json.loads(saved.read_text())) == 300


async def test_a_display_only_payload_saves_the_text(data_dir: Path) -> None:
    """A subagent's transcript is only what the client shows, its answer is the result."""
    text = "\n".join(f"line {i}" for i in range(300))
    result = wrap_tool_output(
        ToolOutput(data={"messages": ["..."]}, formatted=text, display_only=True)
    )

    spilled = await _spill(result)

    assert isinstance(spilled, ToolReturn)
    preview = spilled.return_value
    assert isinstance(preview, str)
    assert "read_document from offset=" in preview
    assert _saved(preview, data_dir).read_text() == text


async def test_a_result_that_fits_passes_untouched(data_dir: Path) -> None:
    result = wrap_tool_output(ToolOutput(data=[_Row("a")], formatted="a"))

    assert await _spill(result) is result
    assert not (data_dir / "tmp").exists()


async def test_saved_results_stay_within_the_tmp_cap(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The oldest saved result makes room, and one that cannot fit is only clamped."""
    monkeypatch.setattr(settings.tmp, "max_bytes", 6_000)
    results = tmp_dir(data_dir, "c1") / ".tool-results"

    first = _saved(await _spill("a\n" * 2_000, call_id="call-1"), data_dir)
    second = _saved(await _spill("b\n" * 2_000, call_id="call-2"), data_dir)

    assert sorted(results.iterdir()) == [second]
    assert not first.exists()

    clamped = await _spill("c\n" * 4_000, call_id="call-3")

    assert "characters left out here" in clamped
    assert sorted(results.iterdir()) == [second]


async def test_a_content_list_bounds_its_text_and_keeps_its_images(
    data_dir: Path,
) -> None:
    image = BinaryContent(data=b"png", media_type="image/png")

    spilled = await _spill(["line\n" * 2_000, image])

    assert isinstance(spilled, list)
    preview, kept = spilled
    assert len(preview) <= 1_000
    assert _saved(preview, data_dir).read_text() == "line\n" * 2_000
    assert kept is image


async def test_the_approval_note_wraps_the_spill_whatever_the_list_order(
    data_dir: Path,
) -> None:
    """The order is declared, so the note is added after the text was bounded."""
    spill, notes = ToolOutputSpill(max_chars=1_000), ApprovalNotes()
    combined = CombinedCapability[UserDeps]([spill, notes])

    assert combined.capabilities == [notes, spill]

    preview, note = await _spill("x\n" * 2_000, capability=combined, note="Keep it.")

    assert len(preview) <= 1_000
    assert "is saved at" in preview
    assert note == approval_note_text("Keep it.")
