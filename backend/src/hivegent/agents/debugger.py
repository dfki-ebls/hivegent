"""The admin chat debugger's view of one stored conversation.

A conversation is flattened into numbered steps, one per message part plus one
per model response, which the debugger agent reads through an overview first
and then step by step.  Timings are derived from the timestamps pydantic-ai
stores, as neither it nor the Vercel AI protocol records durations.

Findings leave the server only as a report that :class:`LeakCheck` holds
against the conversation, so nothing concrete from it is repeated verbatim.
"""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from itertools import count
from typing import Literal, Self

from pydantic_ai import Agent, FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    CompactionPart,
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from spellchecker import SpellChecker

from ..config import settings
from ..db.conversations import CHAT_ERROR_KEY, render_prompt
from ..tmp import RESULTS_DIR
from ..tools.formatting import hint_suffix, truncate_around, truncate_line
from ..types import DebugReport, ToolSchema
from .app import default_model_settings, guards

__all__ = [
    "DebugDeps",
    "LeakCheck",
    "Step",
    "conversation_steps",
    "debug_agent",
    "report_agent",
    "write_report",
]

type StepKind = Literal[
    "instructions",
    "user",
    "assistant",
    "thinking",
    "model",
    "call",
    "return",
    "retry",
    "compaction",
    "error",
]

_PREVIEW_CHARS = 120
_OVERVIEW_ROWS = 150
_PAGE_CHARS = 8000
_SEARCH_CHARS = 240
_SEARCH_MATCHES = 30
_FAILURE_CHARS = 600

# What `ToolOutputSpill` names in the note that replaces a spilled result.
_SPILL_MARKER = f"{RESULTS_DIR}/"

DEBUGGER_INSTRUCTIONS = """\
You analyze one conversation between a user and the Hivegent agent, a RAG \
assistant over the user's documents, to drive the development of Hivegent. \
An administrator asks you about it.

The conversation is split into numbered steps (#0, #1, ...). Start with \
`get_overview` and `list_failures`, then read single steps with `get_step` \
and find patterns with `search_steps`. Check a call against its schema with \
`get_tool_schema`. A step's time is how long it took: for a model step the \
wait for its first token, for a return or retry the generation of its call \
plus the tool's execution.

Look for:
- tool errors: invalid arguments, failures, timeouts, misleading error messages
- agentic loop issues: repeated or redundant calls, retries that never \
recover, wrong tool choice, ignored or misread tool output, giving up early, \
truncated responses, context overflow and compaction, oversized outputs
- feature gaps: what the user wanted that no tool or instruction supports
- friction: anything that made the user's problem harder to solve than it \
should be

Name the first step where things went wrong and the root cause rather than \
its symptoms. Back every claim with step references and say when evidence is \
inconclusive. Write each finding as: title, category, severity, steps, \
evidence, root cause, suggested fix, the part of Hivegent it concerns (tool, \
instructions, frontend, ...), and how to verify the fix.
"""

REPORT_INSTRUCTIONS = """\
You turn a debugging session about a conversation with the Hivegent agent \
into one self-contained Markdown report for a developer or coding agent who \
has the Hivegent source code but must never see the conversation's data.

Write in English. Keep the findings, their categories, severities, root \
causes, suggested fixes, verification steps, and step references (#12). \
Keep what belongs to Hivegent itself: tool names, parameter names, field \
names, error types, and the behaviour of the agent.

Replace every concrete datum from the conversation with a typed placeholder \
such as <PERSON_1>, <ORGANIZATION_1>, <LOCATION_1>, <DOCUMENT_1>, <PATH_1>, \
<URL_1>, <EMAIL_1>, <NUMBER_1>, <DATE_1>, <TOPIC_1>, or <TEXT_1>: names, \
organizations, places, titles, file names, paths, URLs, identifiers, \
numbers, dates, and the subject matter. The same value always gets the same \
placeholder. Never quote what the user, a document, or a tool returned; \
describe its shape instead, e.g. "a German question about <TOPIC_1>" or "a \
table with <NUMBER_1> rows". Answer with the report alone.
"""


@dataclass(slots=True, frozen=True)
class Step:
    """One numbered step of a conversation.

    ``status`` is the outcome of a return and the finish reason of a model
    response, and ``took`` how long the step took where the timestamps tell.
    """

    index: int
    kind: StepKind
    text: str
    tool: str | None = None
    call_id: str | None = None
    status: str | None = None
    took: timedelta | None = None
    usage: RequestUsage | None = None

    @property
    def failed(self) -> bool:
        """Whether the step is a failure the chat shows as one."""
        return self.kind in ("retry", "error") or (
            self.kind == "return" and self.status != "success"
        )


def _since(start: datetime | None, end: datetime) -> timedelta | None:
    return None if start is None else end - start


def conversation_steps(messages: Iterable[ModelMessage]) -> Iterator[Step]:
    """Flatten *messages* into numbered steps.

    Instructions are a step where they change, and a run error stored for
    the chat banner one after the message holding it.
    """
    n = count()
    instructions: str | None = None
    sent: datetime | None = None
    called: dict[str, datetime] = {}

    for message in messages:
        if isinstance(message, ModelRequest):
            sent = message.timestamp

            if message.instructions and message.instructions != instructions:
                instructions = message.instructions
                yield Step(next(n), "instructions", instructions)

            for part in message.parts:
                match part:
                    case UserPromptPart():
                        yield Step(next(n), "user", render_prompt(part))

                    case BaseToolReturnPart():
                        yield Step(
                            next(n),
                            "return",
                            part.model_response_str(),
                            tool=part.tool_name,
                            call_id=part.tool_call_id,
                            status=part.outcome,
                            took=_since(called.get(part.tool_call_id), part.timestamp),
                        )

                    case RetryPromptPart():
                        yield Step(
                            next(n),
                            "retry",
                            part.model_response(),
                            tool=part.tool_name,
                            call_id=part.tool_call_id,
                            took=_since(called.get(part.tool_call_id), part.timestamp),
                        )

                    case _:
                        pass

        else:
            yield Step(
                next(n),
                "model",
                f"model={message.model_name} "
                f"input_tokens={message.usage.input_tokens} "
                f"output_tokens={message.usage.output_tokens}",
                status=message.finish_reason or message.state,
                took=_since(sent, message.timestamp),
                usage=message.usage,
            )

            for part in message.parts:
                match part:
                    case TextPart(content=content) if content.strip():
                        yield Step(next(n), "assistant", content)

                    case ThinkingPart(content=content) if content.strip():
                        yield Step(next(n), "thinking", content)

                    case BaseToolCallPart():
                        called[part.tool_call_id] = message.timestamp
                        yield Step(
                            next(n),
                            "call",
                            part.args_as_json_str(),
                            tool=part.tool_name,
                            call_id=part.tool_call_id,
                        )

                    case CompactionPart(content=content):
                        yield Step(next(n), "compaction", content or "(opaque)")

                    case _:
                        pass

        if error := (message.metadata or {}).get(CHAT_ERROR_KEY):
            yield Step(next(n), "error", str(error))


_TOKEN = re.compile(r"\w+(?:[@./:+'-]+\w+)*")
# Placeholders and step references, which a report may repeat freely.
_PROTECTED = re.compile(r"<[A-Z][A-Z0-9_]*>|#\d+")
_SPECIFIC = re.compile(r"\d.*\d|[@./:]")


def _words(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(text)}


@cache
def _dictionaries() -> tuple[SpellChecker, ...]:
    """The English and German dictionaries, the languages Hivegent speaks, loaded once."""
    return tuple(SpellChecker(language=language) for language in ("en", "de"))


def _rare(word: str) -> bool:
    """Whether no dictionary knows *word*, as they keep only the common words."""
    return not any(word in dictionary for dictionary in _dictionaries())


@dataclass(slots=True, frozen=True)
class LeakCheck:
    """The words a report must not repeat from a conversation.

    A word of the conversation is its data when it holds digits or address
    characters, is written capitalized mid-sentence like a name, or is rare,
    while words Hivegent itself uses, such as its tool names, never are.
    A quote leaks through such words too, as one made of common words alone
    identifies nothing.  Plain word matching, so it catches any kind of datum
    the conversation held without a model to recognize it.
    """

    specific: frozenset[str]

    @classmethod
    def of(cls, texts: Iterable[str], own: Iterable[str]) -> Self:
        """The check against everything in *texts*, allowing the words of *own*."""
        allowed = {word for text in own for word in _words(text)}
        allowed |= {piece for word in allowed for piece in re.split(r"[\W_]+", word)}
        specific: set[str] = set()
        plain: set[str] = set()

        for text in texts:
            for match in _TOKEN.finditer(text):
                token = match.group()
                # A short window, as a whole prefix per token is quadratic.
                start = match.start()
                before = text[max(start - 16, 0) : start].rstrip()[-1:]
                named = token[0].isupper() and (before.isalnum() or before == ",")
                (specific if named or _SPECIFIC.search(token) else plain).add(
                    token.casefold()
                )

        specific.update(filter(_rare, plain - allowed))

        return cls(frozenset(word for word in specific - allowed if len(word) > 2))

    def leaks(self, report: str) -> list[str]:
        """The distinct words of *report* repeated from the conversation."""
        return list(
            dict.fromkeys(
                token
                for segment in _PROTECTED.split(report)
                for token in _TOKEN.findall(segment)
                if token.casefold() in self.specific
            )
        )

    @staticmethod
    def mask(report: str, leaks: Iterable[str]) -> str:
        """Replace each of *leaks* in *report* with a numbered ``<REDACTED_n>``.

        Longest first, so a word never masks part of a longer one.
        """
        for n, leak in enumerate(sorted(leaks, key=len, reverse=True), start=1):
            report = re.sub(
                rf"(?<![\w<#]){re.escape(leak)}(?![\w>])",
                f"<REDACTED_{n}>",
                report,
                flags=re.IGNORECASE,
            )

        return report


@dataclass(slots=True, frozen=True)
class DebugDeps:
    """The conversation under analysis and the tools its agent had."""

    steps: Sequence[Step]
    tools: Sequence[ToolSchema]

    def leak_check(self) -> LeakCheck:
        """The check of a report against the conversation's data.

        Hivegent's own words, its tool schemas and these instructions, are
        allowed, while the model's identity and token counts are no data.
        """
        return LeakCheck.of(
            (step.text for step in self.steps if step.kind != "model"),
            own=[
                json.dumps([schema.model_dump() for schema in self.tools]),
                DEBUGGER_INSTRUCTIONS,
                REPORT_INSTRUCTIONS,
            ],
        )


def _row(step: Step) -> str:
    """One overview line naming *step* and previewing its text."""
    tool = f" {step.tool}" if step.tool else ""
    status = f" [{step.status}]" if step.status else ""
    took = f" {step.took.total_seconds():.1f}s" if step.took is not None else ""
    # Only the head is split, as the preview never reaches past it.
    preview = truncate_line(
        " ".join(step.text[: _PREVIEW_CHARS * 2].split()), _PREVIEW_CHARS
    )

    return f"#{step.index} {step.kind}{tool}{status}{took} {len(step.text)} chars: {preview}"


def _listing(items: Iterable[str], separator: str = ", ") -> str:
    return separator.join(items) or "none"


def _refs(steps: Iterable[Step]) -> str:
    return _listing(f"#{step.index}" for step in steps)


def _summary(steps: Sequence[Step]) -> str:
    """Counts the analysis would otherwise have to tally itself."""
    by_kind = Counter(step.kind for step in steps)
    calls = Counter(step.tool for step in steps if step.kind == "call")
    failures = Counter(step.tool for step in steps if step.failed)
    finishes = Counter(step.status for step in steps if step.kind == "model")
    usages = [step.usage for step in steps if step.usage is not None]
    identical: defaultdict[tuple[str | None, str], list[Step]] = defaultdict(list)

    for step in steps:
        if step.kind == "call":
            identical[step.tool, step.text].append(step)

    lines = [
        _listing(f"{kind}: {total}" for kind, total in by_kind.items()),
        (
            f"tokens: input {sum(u.input_tokens for u in usages)} "
            f"(peak {max((u.input_tokens for u in usages), default=0)}), "
            f"output {sum(u.output_tokens for u in usages)}"
        ),
        "finish reasons: "
        + _listing(f"{reason}: {total}" for reason, total in finishes.items()),
        "tool calls (failures): "
        + _listing(
            f"{tool}: {total} ({failures[tool]})" for tool, total in calls.items()
        ),
        "repeated identical calls: "
        + _listing(
            (
                f"{tool} at {_refs(group)}"
                for (tool, _), group in identical.items()
                if len(group) > 1
            ),
            "; ",
        ),
        f"failures: {_refs(step for step in steps if step.failed)}",
        "spilled outputs: "
        + _refs(
            step
            for step in steps
            if step.kind == "return" and _SPILL_MARKER in step.text
        ),
        f"compactions: {_refs(step for step in steps if step.kind == 'compaction')}",
    ]

    return "\n".join(lines)


def _step(steps: Sequence[Step], index: int) -> Step:
    if not 0 <= index < len(steps):
        raise ModelRetry(
            f"There is no step #{index}, the steps are #0 to #{len(steps) - 1}."
        )

    return steps[index]


debugger_toolset: FunctionToolset[DebugDeps] = FunctionToolset()


@debugger_toolset.tool
def get_overview(ctx: RunContext[DebugDeps], start: int = 0) -> str:
    """Summarize the conversation and list its steps one line each.

    Each line gives the step's kind, tool, status, time, size, and a preview.
    The summary leads the first page only.

    Args:
        start: The first step to list, to page through a long conversation.
    """
    steps = ctx.deps.steps
    end = min(start + _OVERVIEW_ROWS, len(steps))
    rows = "\n".join(map(_row, steps[start:end]))
    summary = f"{_summary(steps)}\n\n" if start == 0 else ""
    more = hint_suffix(
        [
            f"steps #{start} to #{end - 1} of {len(steps)}",
            f"call get_overview(start={end}) for more",
        ]
        if end < len(steps)
        else []
    )

    return f"{summary}{rows}{more}"


@debugger_toolset.tool
def list_failures(ctx: RunContext[DebugDeps]) -> str:
    """List every failed tool call, retry, and run error with its call and error text."""
    steps = ctx.deps.steps
    calls = {step.call_id: step for step in steps if step.kind == "call"}
    blocks: list[str] = []

    for step in filter(lambda step: step.failed, steps):
        call = calls.get(step.call_id)
        origin = (
            f"\ncall #{call.index}: {truncate_line(call.text, _PREVIEW_CHARS)}"
            if call is not None
            else ""
        )
        error = truncate_line(step.text, _FAILURE_CHARS)
        blocks.append(f"{_row(step)}{origin}\nerror: {error}")

    return "\n\n".join(blocks) or "No failures."


@debugger_toolset.tool
def get_step(ctx: RunContext[DebugDeps], index: int, offset: int = 0) -> str:
    """Read the full text of one step, a page at a time.

    Args:
        index: The step number, without the `#`.
        offset: The character to start from, to read on in a long step.
    """
    step = _step(ctx.deps.steps, index)
    end = offset + _PAGE_CHARS
    more = hint_suffix(
        [f"{len(step.text) - end} more chars", f"call get_step({index}, offset={end})"]
        if end < len(step.text)
        else []
    )

    return f"{_row(step)}\n\n{step.text[offset:end]}{more}"


@debugger_toolset.tool
def search_steps(
    ctx: RunContext[DebugDeps], pattern: str, tool: str | None = None
) -> str:
    """Find the steps whose text matches a regular expression, ignoring case.

    Args:
        pattern: A Python regular expression.
        tool: Only search the calls, returns, and retries of this tool.
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ModelRetry(f"Invalid pattern: {exc}") from exc

    hits = [
        (step, match)
        for step in ctx.deps.steps
        if tool is None or step.tool == tool
        if (match := regex.search(step.text))
    ]
    blocks = [
        f"#{step.index} {step.kind} {step.tool or ''}: "
        + truncate_around(step.text, match.start(), _SEARCH_CHARS)
        for step, match in hits[:_SEARCH_MATCHES]
    ]
    more = hint_suffix(
        [f"{len(hits) - _SEARCH_MATCHES} more steps match"]
        if len(hits) > _SEARCH_MATCHES
        else []
    )

    return "\n\n".join(blocks) + more if blocks else "No step matches."


@debugger_toolset.tool
def get_tool_schema(ctx: RunContext[DebugDeps], name: str) -> str:
    """Describe one of the agent's tools: its description and parameter schema.

    Args:
        name: The tool name as it appears in the steps.
    """
    schemas = {schema.name: schema for schema in ctx.deps.tools}

    if (schema := schemas.get(name)) is None:
        raise ModelRetry(
            f"Unknown tool {name!r}, known are: {', '.join(sorted(schemas))}"
        )

    return f"{schema.description}\n\n{json.dumps(schema.parameters, indent=1)}"


debug_agent = Agent(
    name="debug",
    deps_type=DebugDeps,
    retries=settings.llm.retries,
    model_settings=default_model_settings,
    capabilities=guards,
    toolsets=[debugger_toolset],
    instructions=DEBUGGER_INSTRUCTIONS,
)
"""Analyzes a stored conversation for the admin chat debugger."""

report_agent: Agent[None, str] = Agent(
    name="report",
    retries=settings.llm.retries,
    model_settings=default_model_settings,
    capabilities=guards,
    instructions=REPORT_INSTRUCTIONS,
)
"""Condenses a chat debugger session into a report free of the conversation's data."""

_LEAKED = """\
These words still repeat the conversation's data: {leaks}

Answer with the whole report again, each of them replaced by a placeholder.\
"""


async def write_report(
    session: str, check: LeakCheck, model: Model, model_settings: ModelSettings
) -> DebugReport:
    """Condense *session* into a report that passes *check*.

    The model gets one more turn to replace what it leaked, and whatever it
    leaks after that is masked.
    """
    result = await report_agent.run(session, model=model, model_settings=model_settings)

    if leaks := check.leaks(result.output):
        result = await report_agent.run(
            _LEAKED.format(leaks="; ".join(leaks)),
            message_history=result.all_messages(),
            model=model,
            model_settings=model_settings,
        )
        leaks = check.leaks(result.output)

    return DebugReport(markdown=LeakCheck.mask(result.output, leaks), masked=len(leaks))
