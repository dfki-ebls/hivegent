"""Run-loop safeguards composed onto the agents, configured for Hivegent.

The generic guards live in `exar.pydantic_ai.guards`, and this module words their
notes in the run's language and supplies what only Hivegent knows:

* :data:`INCOMPLETE_TOOL_CALLS` and :data:`VISIONLESS_IMAGES` configure
  `IncompleteToolCallGuard` and `VisionlessImages` with localized notes, the
  last with the vision of the run's LLM config, :data:`PROMPT_IMAGE_LIMIT`
  does so for `PromptImageLimit` with the image cap of the run's LLM config,
  and :func:`limit_notes` for `LimitNotes`.
* :class:`ConversationSpill` is the `ToolOutputLimits` of pydantic-ai-harness
  saving a larger return whole under the conversation's ``/tmp/.tool-results/``,
  which also keeps the structured ``DataChunk`` the client cites from unless it
  is too large for the frontend.
* :class:`EnglishToolCalls` validates and executes every tool call in the
  default language, so tool results and retries stay English whatever the
  interface language of the request the run serves.
* :data:`OFFERED_TOOLS` hides from the model every tool only a program is handed
  (``settings.tools.sandbox_only``), and from a model without vision every tool
  marked ``seeing`` (``read_binary_document``), on every agent, so neither a run
  nor a subagent offers one it cannot use.
* :data:`HOST_GATE` holds every tool of every agent to the policy of the exar
  run it runs in, the host policy of its deps (``UserDeps.run_config``), the built-in, plugin, and external MCP tools alike:
  it hides what the mode does not offer and the run withholds (the withheld
  names, people-only tools from a service principal), failing closed for a
  tool that declares no effect unless `exar.pydantic_ai.DeclaredEffects`
  declares it, and asks before each call the policy asks about.
* :data:`TOOL_OUTPUT_FENCE` is the `ToolOutputFence` that marks what a tool
  returned as data on the wire, inside :data:`FENCE_TAG` tags the text cannot
  close, so what a document or page carries reads as material rather than
  instructions.

All hook the pydantic-ai agent loop, so they touch only the agent path, the
framework-neutral tools and their FastMCP adapter are unaffected.  All ride on
the agents themselves (see ``agents/app.py``), because a localized tool
result, a truncated call, a request the gateway will reject, a runaway return,
a spent budget, and injected instructions are hazards on every run.

What reshapes the conversation does it on the wire and nowhere else: the
messages handed to the model are what it sees, while the graph keeps its own
and records those, so a note never enters the stored conversation.  The spill
is the deliberate exception, the bounded return is recorded in place of the
original.
"""

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, override

from exar.memory import digest
from exar.memory.mounts import render
from exar.pydantic_ai import EffectGate
from exar.pydantic_ai.guards import (
    IncompleteToolCallGuard,
    LimitNotes,
    PromptImageLimit,
    ToolOutputFence,
    VisionlessImages,
)
from exar.text import plural
from exar.tools import Retry
from exar.tools.batch import unpack
from pydantic_ai.capabilities import (
    AbstractCapability,
    PrepareTools,
    RawToolArgs,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai_harness import ToolOutputLimits
from pydantic_ai_harness.tool_output_limits import (
    GREP_TOOL_NAME,
    READ_TOOL_NAME,
    Band,
    Spill,
    Truncate,
)

from ..config import settings
from ..llm_config import LlmConfig, chat_llm, run_max_images
from ..prompts import (
    FENCE_INSTRUCTIONS,
    FENCE_TAG,
)
from ..shared.l10n import (
    DEFAULT_LANGUAGE,
    Language,
    Localized,
    current_language,
    format_bytes,
    use_language,
)
from ..tmp import RESULTS_DIR, TMP_PREFIX, save_result
from .common import ASK_USER_TOOL, UserDeps
from .output import ToolOutputChunk, with_data_chunk
from .policy import flags_of

__all__ = [
    "HOST_GATE",
    "INCOMPLETE_TOOL_CALLS",
    "OFFERED_TOOLS",
    "PROMPT_IMAGE_LIMIT",
    "SPILL_TOOLS",
    "TOOL_OUTPUT_FENCE",
    "VISIONLESS_IMAGES",
    "ConversationSpill",
    "EnglishToolCalls",
    "SavedResults",
    "cut_text",
    "limit_notes",
    "max_images",
    "spill_text",
]


def _offered(
    ctx: RunContext[UserDeps], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    """*tool_defs* without those only a program is handed or the model cannot use.

    A model without vision gets no tool that only returns what it would have to see.
    """
    vision = _vision(ctx)

    return [
        td
        for td in tool_defs
        if td.name not in settings.tools.sandbox_only
        and (vision or not flags_of(td.metadata).seeing)
    ]


OFFERED_TOOLS: AbstractCapability[UserDeps] = PrepareTools(_offered, id="offered-tools")

HOST_GATE = EffectGate[UserDeps]()
"""Holds every tool call of a run to the policy of its exar run, the one gate of every agent and of ``invoke_agent_tool``."""


def _run_language(ctx: RunContext[Any]) -> Language:
    """The run's own language where its deps carry one, else the request's.

    The guards ride on every agent, including ``title_agent``, whose runs have
    no :class:`UserDeps` to state a language.
    """
    if isinstance(ctx.deps, UserDeps):
        return ctx.deps.language

    return current_language()


def _incomplete_tool_call(ctx: RunContext[Any], max_tokens: int | None) -> str:
    return Localized(
        en=(
            f"Model token limit ({max_tokens or 'provider default'}) exceeded "
            "while generating a tool call, resulting in incomplete arguments."
        ),
        de=(
            f"Token-Limit des Modells ({max_tokens or 'Standard des Anbieters'}) "
            "beim Erzeugen eines Tool-Aufrufs überschritten, die Argumente sind "
            "daher unvollständig."
        ),
    )[_run_language(ctx)]


def _run_llm(ctx: RunContext[Any]) -> LlmConfig | None:
    """The model config of the run, the default chat model's where it names none."""
    return (ctx.deps.llm if isinstance(ctx.deps, UserDeps) else None) or chat_llm()


def _vision(ctx: RunContext[Any]) -> bool:
    """Whether the run's model sees images, a run without a model config assumed to."""
    llm = _run_llm(ctx)

    return llm is None or llm.vision


def max_images(ctx: RunContext[Any]) -> int | None:
    """The image cap of the run's model, the global one without a model config."""
    return run_max_images(_run_llm(ctx))


INCOMPLETE_TOOL_CALLS = IncompleteToolCallGuard[Any](message=_incomplete_tool_call)
"""Fails a turn the token limit cut off mid tool call, in the run's language, since the user reads the error.

Its partial response has already streamed, so the turn is not compacted and
retried in place, and compacting by hand is the remedy while ``llm.max_tokens``
is unset on the main tier, which is its documented default.
"""

VISIONLESS_IMAGES = VisionlessImages[Any](vision=_vision)
"""Sends every image as a note to a run whose LLM config has no vision.

The note stays exar's English, like every short control note only the model reads.
"""

PROMPT_IMAGE_LIMIT = PromptImageLimit[Any](max_images=max_images)
"""Holds a request to the image cap of the run's model, the others noted in exar's English."""


def limit_notes(max_chars: int) -> LimitNotes[Any]:
    """Note a nearly spent request budget or retry count in exar's English, retry prompts bounded to *max_chars*.

    See "One budget per turn" in the README.
    """
    return LimitNotes[Any](max_chars=max_chars)


class EnglishToolCalls(AbstractCapability[Any]):
    """Validate and execute every tool call in the default language.

    Tool results and retries are read by the model alone, and the workspace,
    URL policy and subagent code a tool reaches words its receipts and errors
    in the ambient language of the request.  Pinning that language for the
    duration of the call keeps every tool result English, the subagent runs a
    tool starts included.
    """

    async def wrap_tool_validate(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
        handler: WrapToolValidateHandler,
    ) -> ValidatedToolArgs:
        """Validate the arguments in the default language."""
        with use_language(DEFAULT_LANGUAGE):
            return await handler(args)

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """Execute the tool in the default language."""
        with use_language(DEFAULT_LANGUAGE):
            return await handler(args)


SPILL_TOOLS: Mapping[str, str] = {
    READ_TOOL_NAME: "Reads back a tool result saved under /tmp/.tool-results.",
    GREP_TOOL_NAME: "Finds the lines of a tool result saved under /tmp/.tool-results.",
}
"""The tools `ToolOutputLimits` adds to query a saved result, and what each does."""


@dataclass(frozen=True, slots=True)
class SavedResults:
    """The `OverflowStore` of the tool results a conversation saved under ``/tmp/.tool-results/``.

    Args:
        folder: The folder ``/tmp`` names.
    """

    folder: Path

    async def write(self, key: str, data: bytes, suffix: str = "txt") -> str:
        """Save *data* as a file named by a digest of *key*, returning its ``/tmp`` path."""
        name = f"{digest(key)[:16]}.{suffix}"

        return await save_result(self.folder, name, data.decode())

    async def read(self, handle: str) -> bytes:
        """The saved result the ``/tmp`` path *handle* names."""
        name = PurePosixPath(handle).name

        if handle != render(TMP_PREFIX, f"{RESULTS_DIR}/{name}"):
            raise FileNotFoundError(handle)

        return await asyncio.to_thread((self.folder / RESULTS_DIR / name).read_bytes)


async def spill_text(folder: Path | None, text: str, max_chars: int) -> str:
    """*text*, cut to *max_chars* once longer, its whole saved among the results of the ``/tmp`` at *folder*.

    For a text no tool returns, which :class:`ConversationSpill` never sees.
    The file is named by the text's digest, and the cut ends on a note naming
    it, or only the length of the text without *folder* or when the file does
    not fit.
    """
    if len(text) <= max_chars or folder is None:
        return cut_text(text, max_chars)

    saved: str | None = None

    with contextlib.suppress(Retry):
        saved = await SavedResults(folder).write(text, text.encode())

    return cut_text(text, max_chars, saved)


def cut_text(text: str, max_chars: int, saved: str | None = None) -> str:
    """*text*, cut to *max_chars* once longer, with a note naming where *saved* holds its whole.

    >>> cut = cut_text("x" * 100, 80)
    >>> len(cut), cut.endswith("[Cut short of its 100 characters.]")
    (80, True)
    """
    if len(text) <= max_chars:
        return text

    where = (
        ""
        if saved is None
        else f", saved whole at {saved}, which read_tool_result reads"
    )
    note = f"\n\n[Cut short of its {len(text):,} characters{where}.]"

    return text[: max(0, max_chars - len(note))] + note


@dataclass(kw_only=True)
class ConversationSpill(ToolOutputLimits[UserDeps]):
    """Bound both channels of a tool return, saving the whole of a larger one.

    A tool return carries two channels: the LLM-facing text, whose leading
    string `ToolOutputLimits` bounds and saves under the conversation's
    ``/tmp/.tool-results/`` (:class:`SavedResults`), keeping the files after
    it, and the structured payload the frontend reads in ``ToolReturn.metadata``
    (a ``DataChunk``).  The chunk is kept as it is within ``max_data_bytes``,
    since the client cites from it, and dropped past that, since the frontend
    loads every chunk of a conversation and the database stores it.
    A run without a ``/tmp`` only clamps the text.

    The decision is the harness's rather than the model's: a tool argument that
    asked the model to route a result into a file was misread time and again.
    """

    max_chars: int
    max_data_bytes: int
    bands: Sequence[Band] = field(init=False, default=())

    def __post_init__(self) -> None:
        clamp = Truncate(max_chars=self.max_chars)
        action = (
            clamp
            if self.store is None
            else Spill(preview_chars=self.max_chars, then=clamp)
        )
        self.bands = [Band(over=self.max_chars, action=action)]
        super().__post_init__()

    @override
    async def for_run(self, ctx: RunContext[UserDeps]) -> ToolOutputLimits[UserDeps]:
        """The spill of the run, saving into its ``/tmp`` if it has one."""
        tmp = ctx.deps.tmp

        if tmp is None:
            return await super().for_run(ctx)

        return replace(self, store=SavedResults(tmp.path))

    @override
    async def after_tool_execute(
        self,
        ctx: RunContext[UserDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        """Wrap the typed data in the chunk the client receives, then bound both channels."""
        returned = with_data_chunk(result, call.tool_call_id)
        unpacked = unpack(returned)
        bounded = replace(returned, return_value=unpacked.text)
        reduced = await super().after_tool_execute(
            ctx, call=call, tool_def=tool_def, args=args, result=bounded
        )
        chunk = returned.metadata
        too_large = (
            isinstance(chunk, ToolOutputChunk)
            and len(chunk.data_json()) > self.max_data_bytes
        )

        if reduced is bounded and not too_large:
            return returned

        if reduced is not bounded and (data := await self._data(call, chunk)):
            reduced = replace(reduced, return_value=f"{reduced.return_value}\n\n{data}")

        value = (
            [reduced.return_value, *unpacked.files]
            if unpacked.files
            else reduced.return_value
        )

        return replace(
            returned, return_value=value, metadata=None if too_large else chunk
        )

    async def _data(self, call: ToolCallPart, chunk: object) -> str | None:
        """Save the structured data of a spilled result as JSON, returning the note naming it.

        ``None`` without a store, without structured data, or when the data
        does not fit among the saved results.
        """
        if not isinstance(self.store, SavedResults) or not isinstance(
            chunk, ToolOutputChunk
        ):
            return None

        encoded = chunk.data_json()

        try:
            path = await self.store.write(call.tool_call_id, encoded, "json")
        except Retry:
            return None

        parts = chunk.data if isinstance(chunk.data, list | tuple) else (chunk.data,)
        count = (
            f", {len(parts)} {plural(len(parts), 'entry', 'entries')}"
            if isinstance(chunk.data, list | tuple)
            else ""
        )
        clipped = (
            " The tool had already cut it short of what was asked for,"
            " so raise its limit or narrow the request for the rest."
            if any(getattr(part, "truncated", False) is True for part in parts)
            else ""
        )

        return (
            f"[The structured data, {format_bytes(len(encoded))}{count}, is saved as JSON at {path}."
            f"{clipped} Query it with jq or run_python, or search it with grep,"
            " rather than calling the tool again.]"
        )


def _fence_instructions(ctx: RunContext[Any], tag: str) -> str:
    return FENCE_INSTRUCTIONS[_run_language(ctx)].format(tag=tag)


TOOL_OUTPUT_FENCE: ToolOutputFence[Any] = ToolOutputFence(
    tag=FENCE_TAG, instructions=_fence_instructions, exempt={ASK_USER_TOOL}
)
"""Fences every tool result in :data:`FENCE_TAG`, explained in the run's language, but the user's answers to ``ask_user``."""
