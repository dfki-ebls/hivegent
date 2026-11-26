"""Shared helpers for the agents package."""

import asyncio
import math
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from functools import cache
from typing import Self, TypedDict, override

from exar.agents import RunConfig
from exar.helpers import get_name
from exar.memory.mounts import Mount
from exar.pydantic_ai import toolset
from exar.tools import translate_retry
from exar.tools.web import WebSession
from exar.typing import Policy
from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition, ToolPrepareFunc
from pydantic_ai.toolsets import DynamicToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from ..assistants import Assistant
from ..auth import PROBE
from ..config import settings
from ..db.conversations import OwnedConversation
from ..llm_config import LlmConfig
from ..prompts import format_document_scope
from ..reach import Reach
from ..shared.l10n import DEFAULT_LANGUAGE, Language
from ..skills import skill_search_paths, writable_libraries
from ..store import User, WorkspaceScope
from ..tmp import tmp_dir, tmp_root
from ..tools.base import (
    CommitPolicy,
    SearchPath,
    ToolFunc,
    mounts_of,
    query_hint,
    resolve_accessible_file,
)
from ..types import WRITABLE_MODES, DocumentFilter, Mode
from ..workspace.operations import Root
from .policy import HOST_FLAGS, ToolFlags, host_policy, root_asks, writable

__all__ = [
    "ASK_USER_TOOL",
    "SELF_BOUNDED",
    "WINDOW_MAX_CHARS",
    "AgentTools",
    "ArgsValidator",
    "CompletionRefused",
    "Completions",
    "Hooked",
    "PrefixRunKwargs",
    "RunPrefix",
    "ToolFactory",
    "UserDeps",
    "probe_context",
    "run_context",
    "static_tools",
]

ASK_USER_TOOL = "ask_user"
"""The tool through which the model asks the user, whose answers are the user's own."""

WINDOW_MAX_CHARS = settings.llm.tool_output_max_chars * 9 // 10
"""The window ``read_document`` and ``web_fetch`` show of a text, which a call reads on from by offset.

A whole window has to show rather than spill.  It is measured as rendered,
numbered and clipped, so the tenth of the output bound left over only holds
the headers and hints of the reads one call batches.
"""

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

    max_calls: int = field(
        default_factory=lambda: settings.sandbox.completion_max_calls
    )
    gate: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(
            settings.sandbox.completion_concurrency
        )
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

        if (
            limit is not None
            and ctx.usage.requests + self.pending + 1 + _PARENT_RESERVE > limit
        ):
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
    """Dependencies for user-specific agent operations.

    Every store the run reaches is derived from :attr:`user`, never passed in,
    so a run holds exactly the workspaces the token grants and writes only the
    ones it may write.
    """

    user: User
    mode: Mode
    # The conversation the run belongs to, whose folder `/tmp` names, owned by
    # `user`.  A subagent shares its parent's, and a caller without one (MCP,
    # the admin tool console) has no `/tmp`, so no root claims a path into it.
    conversation: OwnedConversation | None = None
    # The run's interface language, which selects the language of its
    # instructions and injected notes, while tool schemas and results stay
    # English.  The default serves runs without a user interface (MCP).
    language: Language = DEFAULT_LANGUAGE
    # What the conversation hides of each scope's workspace, none of the rest,
    # or with an assistant's allowlist, a filter for every scope the user sees.
    document_filters: Mapping[WorkspaceScope, DocumentFilter] = field(
        default_factory=dict
    )
    # Canonical paths the user pointed the conversation at.  Advisory: they are
    # named to the model and restrict no tool, so they never reach a filter.
    relevant_documents: frozenset[str] = frozenset()
    # Tool names this *request* withheld.  Only the request's half: the
    # operator's `settings.tools.disabled` is global and joins it in
    # `withheld_tools`, so a deps site that forgets this field still cannot
    # hand a program an excluded tool.
    disabled_tools: frozenset[str] = frozenset()
    # The assistant the request named, already applied to the mode, the
    # document selection, and the withheld tools, whose instructions and
    # skills the run reports to the model.
    assistant: Assistant | None = None
    # The chat model the request named, which subagents reuse.  None where no
    # request named one, as on MCP, which then runs on the default.
    llm: LlmConfig | None = None
    # The turn's `complete` calls, shared with its subagents, which `replace`
    # the deps and keep this object.
    completions: Completions = field(default_factory=Completions)
    # The URLs `web_fetch` may open, seeded from the history by the chat path,
    # and the pages it downloaded this turn, which it reads on from, shared
    # with subagents.
    web: WebSession = field(default_factory=WebSession)
    # The roots of :meth:`search_paths` once :meth:`with_roots` resolved them
    # for the run, which listing the skill folders would otherwise repeat.
    roots: tuple[SearchPath, ...] | None = None

    def __post_init__(self) -> None:
        if self.conversation is not None and self.conversation.owner != self.user.id:
            raise ValueError("a run's conversation must belong to its user")

        if self.user.is_service and self.mode != "read":
            raise ValueError("a service principal only ever reads")

    @property
    def can_write(self) -> bool:
        """Whether this run may mutate workspace content."""
        return self.mode in WRITABLE_MODES

    def asks(self, policy: CommitPolicy) -> bool:
        """Whether a change to a root of *policy* waits for the user in this run."""
        return root_asks(policy, self.mode)

    @property
    def tmp(self) -> Root | None:
        """The conversation's ``/tmp``, ``None`` for a caller without one."""
        if self.conversation is None:
            return None

        return tmp_root(tmp_dir(settings.data_dir, self.conversation))

    @property
    def conversation_id(self) -> str | None:
        """pydantic-ai's ``conversation_id`` of a run over these deps, the id of its conversation.

        Passed by every run that starts a turn or compacts it, so its messages,
        its spans' ``gen_ai.conversation.id``, and its subagents' tasks name the
        conversation rather than one pydantic-ai made up or a fork's history carries.
        """
        return None if self.conversation is None else str(self.conversation.id)

    @property
    def withheld_tools(self) -> frozenset[str]:
        """The tools this run may not call: the request's and the operator's."""
        return self.disabled_tools.union(settings.tools.disabled)

    @property
    def confined(self) -> bool:
        """Whether an assistant's allowlist confines the documents this run reaches."""
        return any(f.allowed is not None for f in self.document_filters.values())

    @property
    def policy(self) -> Policy:
        """The host policy of this run, which every surface asks what a tool may do.

        The mode's, narrowed by the withheld names, the people-only tools of a
        service principal, and the derived tools of a confined run.
        """
        return host_policy(
            self.mode,
            withheld=self.withheld_tools,
            service=self.user.is_service,
            confined=self.confined,
        )

    @property
    def run_config(self) -> RunConfig:
        """The config of the exar runs over these deps, whose policy is :attr:`policy`."""
        return RunConfig(policy=self.policy)

    async def with_roots(self) -> Self:
        """These deps with :meth:`search_paths` resolved, off the event loop since it lists directories."""
        return replace(self, roots=await asyncio.to_thread(self._resolve_roots))

    def search_paths(self) -> tuple[SearchPath, ...]:
        """Every root a path tool reads, filters applied: the workspaces, the skills, and ``/tmp``."""
        return self.roots if self.roots is not None else self._resolve_roots()

    def _resolve_roots(self) -> tuple[SearchPath, ...]:
        tmp = self.tmp
        roots = (
            *self.reach.search_paths(),
            *skill_search_paths(self.user),
        )

        return roots if tmp is None else (*roots, tmp.search_path)

    def mounts(self) -> tuple[Mount, ...]:
        """The mounts of :meth:`search_paths`, which the read tools of exar address, search, and read when called."""
        return mounts_of(self.search_paths())

    def writable_roots(self, *, workspace: bool = True) -> tuple[Root, ...]:
        """Every root a mutation may change, each committing by its own policy.

        The writable workspaces and skill libraries in a mode that may write
        them, unless *workspace* leaves them out, and ``/tmp``, which every
        mode may write since it is the conversation's own.  No global skill
        is ever among them.
        """
        stores = [
            store
            for store in self.user.workspaces(writable=True)
            if workspace and writable(store.policy, self.mode)
        ]
        roots = [Root.of(store, self.reach.filter(store)) for store in stores]
        roots.extend(
            lib
            for lib in writable_libraries(self.user)
            if workspace and writable(lib.policy, self.mode)
        )

        if (tmp := self.tmp) is not None:
            roots.append(tmp)

        return tuple(roots)

    @property
    def reach(self) -> Reach:
        """What this run reads, the user's workspaces with the conversation's filters."""
        return Reach(self.user, self.document_filters)

    def describe_document_scope(self) -> str:
        """Render the active document scope as prompt text (``''`` if none).

        Rendered in the run's :attr:`language`, since it is the instruction
        the run reads (``agents.context.DOCUMENT_SCOPE``).

        The hidden half is rendered back from the very :class:`DocumentFilter`
        objects the document tools enforce, so what the model is told cannot
        drift from what its tools return.  The relevant half enforces nothing
        and already arrives canonical, so it carries only what the selection
        cannot say for itself: a selected spreadsheet is named by the markdown
        it was projected to, so the block is the first and cheapest place the
        run can learn that the original is there to be queried instead.
        """
        filters = [
            (store.scope, document_filter)
            for store in self.user.workspaces()
            if (document_filter := self.reach.filter(store)) is not None
        ]
        hidden = frozenset(
            scope.render_filter_entry(entry.path, directory=entry.directory)
            for scope, document_filter in filters
            for entry in document_filter.excluded
        )
        allowed = (
            frozenset(
                scope.render_filter_entry(entry.path, directory=entry.directory)
                for scope, document_filter in filters
                for entry in document_filter.allowed or ()
            )
            if any(
                document_filter.allowed is not None for _, document_filter in filters
            )
            else None
        )
        paths = self.search_paths() if self.relevant_documents else ()
        resolved = (
            (file_path, resolve_accessible_file(paths, file_path))
            for file_path in self.relevant_documents
        )
        relevant = {
            file_path: query_hint(entry[0].mount, entry[1]) if entry else ""
            for file_path, entry in resolved
        }

        return format_document_scope(relevant, hidden, self.language, allowed)


class PrefixRunKwargs(TypedDict):
    """The ``Agent.run`` arguments that :meth:`RunPrefix.run_kwargs` composes."""

    deps: UserDeps
    conversation_id: str | None
    model: Model
    instructions: str | None


@dataclass(slots=True, frozen=True)
class RunPrefix:
    """The inputs that compose a run's prompt prefix.

    Kept together because compaction has to reproduce a chat turn's prefix
    exactly, down to the document scope ``deps`` renders into the
    instructions and the resolved ``llm`` it is sent under, or
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

    def run_kwargs(self) -> PrefixRunKwargs:
        """The arguments every run over this prefix passes to ``Agent.run``, so they cannot drift apart."""
        return {
            "deps": self.deps,
            "conversation_id": self.deps.conversation_id,
            "model": self.model,
            "instructions": self.instructions,
        }


SELF_BOUNDED = math.inf
"""The timeout of a tool that bounds itself, which ``None`` would leave to its toolset's."""


type ArgsValidator = Callable[..., Awaitable[None]]
"""Validates the arguments of a tool call, its run context first, raising `exar.tools.Retry` to refuse them."""


@dataclass(frozen=True, slots=True)
class Hooked:
    """A plain tool with pydantic-ai's per-call hooks, which :class:`AgentTools` installs.

    Args:
        func: The tool.
        args_validator: Runs once the arguments validated and before a call is put
            in front of the user, so it refuses what the tool would refuse or asks per call.
        prepare: Withholds the tool from a run step.
    """

    func: ToolFunc
    args_validator: ArgsValidator | None = None
    prepare: ToolPrepareFunc[UserDeps] | None = None


type ToolFactory = Callable[[RunContext[UserDeps]], Sequence[ToolFunc | Hooked]]
"""Builds the tools of a feature for one run, closing over the run's deps."""


def run_context(
    deps: UserDeps, *, usage_limits: UsageLimits | None = None
) -> RunContext[UserDeps]:
    """The context a toolset is built and a tool called with outside an agent run.

    The placeholder model is never invoked: every tool that talks to a
    language model builds its own from ``deps`` or settings.
    """
    return RunContext(
        deps=deps, model=TestModel(), usage=RunUsage(), usage_limits=usage_limits
    )


def probe_context() -> RunContext[UserDeps]:
    """A run of a principal holding no grant in ``write`` mode, which every factory builds its whole list for.

    What a feature offers is read off the tools it builds for this run, since
    their names, schemas, and effects do not depend on whose run it is.
    """
    return run_context(UserDeps(user=PROBE, mode="write"))


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTools:
    """The tools of a feature, plain functions built per run and served with their effects as metadata.

    Args:
        factory: Builds the tools of one run.
        gateway: Whether every write of the tools lands through the changeset gateway.
        people_only: Whether the tools act on one person's own data, which a
            service principal is never offered.
        derived: Whether the tools return stored text derived from content
            beyond the workspaces, which a confined run is never offered.
        seeing: The names of the tools whose result is only of use to a model that sees images.
        timeout: The bound of each call, which pydantic-ai applies only to the
            tools an agent registers itself unless a toolset states it.  A tool
            that bounds itself declares :data:`SELF_BOUNDED`.
    """

    factory: ToolFactory
    gateway: bool = False
    people_only: bool = False
    derived: bool = False
    seeing: frozenset[str] = frozenset()
    timeout: float | None = field(
        default_factory=lambda: settings.llm.tool_timeout_seconds
    )

    def build(self, ctx: RunContext[UserDeps], /) -> FunctionToolset[UserDeps]:
        """The toolset of the tools of the run *ctx*, which every caller builds them by.

        Built by `exar.pydantic_ai.toolset`, with the effect of each tool as
        its metadata, which ``guards.HOST_GATE`` decides on.  The hooks of
        :class:`Hooked` and the :class:`~hivegent.agents.policy.ToolFlags` of
        each tool are installed on top, and a `exar.tools.Retry` of a validator
        becomes a ``ModelRetry`` like one of its tool.
        """
        items = [
            item if isinstance(item, Hooked) else Hooked(func=item)
            for item in self.factory(ctx)
        ]
        hooks = {get_name(item.func): item for item in items}
        tools = toolset(item.func for item in items)
        tools.timeout = self.timeout

        for name, tool in tools.tools.items():
            hooked = hooks[name]
            tool.args_validator = (
                None
                if hooked.args_validator is None
                else translate_retry(hooked.args_validator, ModelRetry)
            )
            tool.prepare = hooked.prepare
            flags = ToolFlags(
                gateway=self.gateway,
                people_only=self.people_only,
                derived=self.derived,
                seeing=name in self.seeing,
            )
            tool.metadata = {**(tool.metadata or {}), HOST_FLAGS: asdict(flags)}

        return tools

    def toolset(self) -> DynamicToolset[UserDeps]:
        """The toolset an agent composes, built once per run."""
        return DynamicToolset(self.build, per_run_step=False)


@cache
def static_tools(tools: AgentTools) -> tuple[ToolDefinition, ...]:
    """The definition of every tool of *tools*, as the probe run in ``write`` mode is offered them, for listing.

    Definitions only, so nothing built for the probe can ever be called.
    """
    built = tools.build(probe_context())

    return tuple(tool.tool_def for tool in built.tools.values())
