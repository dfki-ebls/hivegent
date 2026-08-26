"""Shared helpers for the agents package."""

import asyncio
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext

from ..config import settings
from ..llm_config import LlmConfig
from ..prompts import format_document_scope
from ..store import Casebase, build_search_paths
from ..tools.base import SearchPath
from ..types import AUTO_APPROVED_MODES, MUTATING_MODES, DocumentFilter, Mode
from .subagent_events import SubagentUpdate

__all__ = [
    "ExploreTaskArg",
    "MemoryContentArg",
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
        and already arrives canonical, so it passes straight through.
        """
        hidden = frozenset(
            store.scope.render_filter_entry(entry)
            for store in self.all_stores
            if (document_filter := self.filter_for_store(store)) is not None
            for entry in document_filter.excluded
        )

        return format_document_scope(self.relevant_documents, hidden)


def scope_instructions(ctx: RunContext[UserDeps]) -> str | None:
    """Dynamic instruction describing the live document scope to the agent.

    Attached to the document-exploration capability so it rides with the tools
    it explains, on both the main agent and the documents subagent. Returns
    ``None`` when no scope is active so pydantic-ai omits the block.
    """
    return ctx.deps.describe_document_scope() or None
