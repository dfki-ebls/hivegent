"""Shared helpers for the agents package."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from ..config import settings
from ..llm_config import LlmConfig
from ..prompts import format_document_scope
from ..store import Casebase, build_search_paths
from ..tools.base import SearchPath, query_hint, resolve_accessible_file
from ..types import AUTO_APPROVED_MODES, MUTATING_MODES, DocumentFilter, Mode
from .subagent_events import SubagentUpdate

__all__ = [
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


@dataclass(slots=True, frozen=True)
class UserDeps:
    """Dependencies for user-specific agent operations."""

    user_id: str
    store: Casebase
    mode: Mode
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
    # operator's `settings.tools.disabled` is global and is unioned in where
    # the surface is built, the way `web_enabled` already is, so a deps site
    # that forgets this field still cannot hand a program an excluded tool.
    disabled_tools: frozenset[str] = frozenset()
    llm: LlmConfig | None = None
    # Sink for live subagent transcript snapshots; set only on the chat path,
    # where the streaming response drains it (None elsewhere disables it).
    subagent_sink: asyncio.Queue[SubagentUpdate] | None = None

    @property
    def all_stores(self) -> tuple[Casebase, ...]:
        """All stores the user has access to (personal + group)."""
        return (self.store, *self.group_stores)

    @property
    def writable_stores(self) -> tuple[Casebase, ...]:
        """The stores the user may mutate (personal + writable groups)."""
        return (self.store, *self.write_group_stores)

    @property
    def can_write(self) -> bool:
        """Whether this run may mutate workspace content."""
        return self.mode in MUTATING_MODES

    @property
    def needs_approval(self) -> bool:
        """Whether a state-changing tool call must be confirmed by the user."""
        return self.mode not in AUTO_APPROVED_MODES

    def search_paths(self, *, writable: bool = False) -> tuple[SearchPath, ...]:
        """Workspace roots for this run's document tools, filters applied.

        *writable* narrows the groups to those the user may mutate.
        """
        return build_search_paths(
            self.store,
            self.write_group_stores if writable else self.group_stores,
            settings.data_dir,
            filter_for_store=self.filter_for_store,
        )

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

        return format_document_scope(relevant, hidden)


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
