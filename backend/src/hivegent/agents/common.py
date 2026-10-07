"""Shared helpers for the agents package."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, override

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, ModelRequestContext

from ..config import settings
from ..l10n import DEFAULT_LANGUAGE, Language
from ..llm_config import LlmConfig
from ..prompts import format_document_scope
from ..store import Casebase, build_search_paths
from ..tmp import tmp_search_path
from ..tools.base import SearchPath, query_hint, resolve_accessible_file
from ..types import AUTO_APPROVED_MODES, MUTATING_MODES, DocumentFilter, Mode
from .subagent_events import SubagentUpdate

__all__ = [
    "CompletionRefused",
    "Completions",
    "ExploreTaskArg",
    "MemoryContentArg",
    "RunPrefix",
    "UserDeps",
    "scope_instructions",
]

ExploreTaskArg = Annotated[
    str,
    Field(description="Natural language description of what to explore or find."),
]
MemoryContentArg = Annotated[
    str,
    Field(description="Full markdown content to persist as memory."),
]

_PARENT_RESERVE = 1
"""Requests a turn's completions leave for the parent, which owes the model the program's result."""


class CompletionRefused(Exception):
    """A completion the turn cannot afford, which its caller may catch and finish without."""


@dataclass(slots=True)
class Completions(AbstractCapability[None]):
    """A turn's ``complete`` calls, shared by its programs and subagents.

    :meth:`slot` counts a call against ``max_calls`` before its first await,
    so ``asyncio.gather`` cannot pass it, and then waits at the ``gate`` on the
    calls in flight.  As a capability of each call's run, it also holds a
    request slot through every model request, retries included: pydantic-ai
    checks the request limit before a request and counts it after, so
    concurrent calls on the turn's usage would each pass the check.  Subagents
    are no such calls, so a subagent that meets the limit still ends the turn.
    """

    max_calls: int = field(default_factory=lambda: settings.sandbox.completion_max_calls)
    gate: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(settings.sandbox.completion_concurrency)
    )
    calls: int = 0
    pending: int = 0

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None]:
        """Count one call and hold a slot in flight for it.

        Raises:
            CompletionRefused: When the turn has used every call.
        """
        if self.calls >= self.max_calls:
            raise CompletionRefused(
                f"complete may be called {self.max_calls} times per turn and "
                "this turn has used them all. Finish without it."
            )

        self.calls += 1

        async with self.gate:
            yield

    @override
    async def wrap_model_request(
        self,
        ctx: RunContext[None],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        """Hold a request slot through one request, refusing one the parent's reserve needs."""
        limit = ctx.usage_limits.request_limit if ctx.usage_limits else None

        if limit is not None and ctx.usage.requests + self.pending + 1 + _PARENT_RESERVE > limit:
            raise CompletionRefused(
                "No request is left for another completion once the parent keeps "
                "one for its response. Finish with the results already obtained."
            )

        self.pending += 1

        # Released right before pydantic-ai counts the request, with no await between.
        try:
            return await handler(request_context)
        finally:
            self.pending -= 1


@dataclass(slots=True, frozen=True)
class UserDeps:
    """Dependencies for user-specific agent operations."""

    user_id: str
    store: Casebase
    mode: Mode
    # The conversation the run belongs to, whose folder `/tmp` names.  A
    # subagent shares its parent's, and a caller without one (MCP, the admin tool
    # console) has no `/tmp`, so no root claims a path into it.
    conversation_id: str | None = None
    # The run's interface language, which selects the language of its
    # instructions and injected notes, while tool schemas and results stay
    # English.  The default serves runs without a user interface (MCP).
    language: Language = DEFAULT_LANGUAGE
    group_stores: tuple[Casebase, ...] = ()
    # The subset of `group_stores` the user may write to; the mutating tools
    # search these instead of every readable one, so a document the user can
    # only read is never offered as a write target.
    write_group_stores: tuple[Casebase, ...] = ()
    document_filter: DocumentFilter | None = None
    group_filters: dict[str, DocumentFilter] = field(default_factory=dict)
    # Canonical paths the user pointed the conversation at.  Advisory: they are
    # named to the model and restrict no tool, so they never reach a filter.
    relevant_documents: frozenset[str] = frozenset()
    # Tool names this *request* withheld.  Only the request's half: the
    # operator's `settings.tools.disabled` is global and joins it in
    # `withheld_tools`, so a deps site that forgets this field still cannot
    # hand a program an excluded tool.
    disabled_tools: frozenset[str] = frozenset()
    llm: LlmConfig | None = None
    # The aux tier as the request resolves it, for one-shot calls a program
    # makes, so a client's own model and provider carry over as they do for
    # titles and captions.  None where no request supplied one.
    aux_llm: LlmConfig | None = None
    # The turn's `complete` calls, shared with its subagents, which `replace`
    # the deps and keep this object.
    completions: Completions = field(default_factory=Completions)
    # Sink for live subagent transcript snapshots; set only on the chat path,
    # where the streaming response drains it (None elsewhere disables it).
    subagent_sink: asyncio.Queue[SubagentUpdate] | None = None

    @property
    def all_stores(self) -> tuple[Casebase, ...]:
        """All stores the user has access to (personal + group)."""
        return (self.store, *self.group_stores)

    @property
    def can_write(self) -> bool:
        """Whether this run may mutate workspace content."""
        return self.mode in MUTATING_MODES

    @property
    def needs_approval(self) -> bool:
        """Whether a state-changing tool call must be confirmed by the user."""
        return self.mode not in AUTO_APPROVED_MODES

    @property
    def tmp(self) -> SearchPath | None:
        """The conversation's ``/tmp``, ``None`` for a caller without one."""
        if self.conversation_id is None:
            return None

        return tmp_search_path(settings.data_dir, self.conversation_id)

    @property
    def withheld_tools(self) -> frozenset[str]:
        """The tools this run may not call: the request's and the operator's."""
        return self.disabled_tools.union(settings.tools.disabled)

    def _workspace_paths(self, *, writable: bool) -> tuple[SearchPath, ...]:
        return build_search_paths(
            self.store,
            self.write_group_stores if writable else self.group_stores,
            settings.data_dir,
            filter_for_store=self.filter_for_store,
        )

    def search_paths(self) -> tuple[SearchPath, ...]:
        """Every root a path tool reads, filters applied: the workspaces and ``/tmp``."""
        tmp = self.tmp
        roots = self._workspace_paths(writable=False)

        return roots if tmp is None else (*roots, tmp)

    def writable_paths(self, *, workspace: bool = True) -> tuple[SearchPath, ...]:
        """Every root a mutation may change, each committing by its own policy.

        The writable workspaces in a mode that may write them, unless
        *workspace* leaves them out, and ``/tmp``, which every mode may write
        since it is the conversation's own.
        """
        tmp = self.tmp
        roots = self._workspace_paths(writable=True) if workspace and self.can_write else ()

        return roots if tmp is None else (*roots, tmp)

    def filter_for_store(self, store: Casebase) -> DocumentFilter | None:
        """Get the applicable DocumentFilter for a specific store.

        Returns the user filter for user stores, the per-group filter
        for group stores (if any), or ``None`` if no filter applies.
        """
        if store.kind == "user":
            return self.document_filter
        return self.group_filters.get(store.id)

    def describe_document_scope(self) -> str:
        """Render the active document scope as prompt text (``''`` if none).

        Rendered in the run's :attr:`language`, since it is part of the prefix.

        The hidden half is rendered back from the very :class:`DocumentFilter`
        objects the document tools enforce, so what the model is told cannot
        drift from what its tools return.  The relevant half enforces nothing
        and already arrives canonical, so it carries only what the selection
        cannot say for itself: a selected spreadsheet is named by the markdown
        it was projected to, so the block is the first and cheapest place the
        run can learn that the original is there to be queried instead.
        """
        hidden = frozenset(
            store.scope.render_filter_entry(entry)
            for store in self.all_stores
            if (document_filter := self.filter_for_store(store)) is not None
            for entry in document_filter.excluded
        )
        paths = self.search_paths()
        resolved = (
            (file_path, resolve_accessible_file(paths, file_path))
            for file_path in self.relevant_documents
        )
        relevant = {
            file_path: query_hint(entry[0], entry[1]) if entry else ""
            for file_path, entry in resolved
        }

        return format_document_scope(relevant, hidden, self.language)


@dataclass(slots=True, frozen=True)
class RunPrefix:
    """The inputs that compose a run's prompt prefix.

    Kept together because compaction has to reproduce a chat turn's prefix
    exactly, down to the document scope ``deps`` renders into the prompt via
    :func:`scope_instructions` and the resolved ``llm`` it is sent under, or
    the provider's cached prefix is thrown away.
    Named for what it is rather than ``AgentRun``, which is a different thing
    in pydantic-ai.
    """

    deps: UserDeps
    capabilities: Sequence[AbstractCapability[UserDeps]]
    # ``None`` where the run states none of its own, as a subagent does: its
    # whole prompt comes from the capability it is composed from.
    instructions: str | None
    # Carried rather than left to the caller because the prefix and the model
    # go together: a different model has a different cache entirely.  The
    # resolved `model` rides along because building one reaches for the
    # lifespan's shared HTTP client, so it is composed once where that is live
    # rather than wherever a prefix happens to be used.
    llm: LlmConfig
    model: Model


def scope_instructions(ctx: RunContext[UserDeps]) -> str | None:
    """Dynamic instruction describing the live document scope to the agent.

    Attached to the document-exploration capability so it rides with the tools
    it explains, on both the main agent and the documents subagent. Returns
    ``None`` when no scope is active so pydantic-ai omits the block.
    """
    return ctx.deps.describe_document_scope() or None
