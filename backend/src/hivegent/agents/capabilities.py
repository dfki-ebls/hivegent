"""Capability composition and the agent feature registry.

A capability is the pydantic-ai v2 primitive that bundles a feature's toolset
with the instructions, hooks, and model settings that belong to it, so a whole
feature reaches the agent through one composable unit.

This module is the single source of truth for the agent's features:
:data:`FEATURES` lists each one as a :class:`Feature` (a capability plus the
modes it is offered in), and every consumer derives from it. The agent run
composes from the capabilities, while the debug/meta REST surface lists and
invokes individual tools extracted from the very same capabilities, so an admin
inspects exactly the tools an agent is built from.

Guidance follows the tools it describes: a block bound to one feature rides on
its capability, a block spanning several is a :class:`SharedInstructions` entry
composed as soon as any of them is live, and only behaviour tied to no
feature at all (personality, language, math) stays agent-level ``instructions``.
State that changes during a conversation is an ``on_change='append'``
instruction (:mod:`.context`), whose changes extend the history instead of
rewriting the prefix.
Prompt text that explains a tool the model was not given is a defect, so nothing
that names a tool belongs in the agent-level set.

Instructions are composed in the run's interface language, chosen explicitly
rather than from the ambient request, while the toolsets and their schemas are
one English set shared by every language.  A feature therefore holds its fixed
instructions as :class:`~hivegent.l10n.Localized` text and resolves them into
plain strings when it builds the run's capability: an instruction callable
reading the language off the run would do the same with less, but pydantic-ai
marks every callable dynamic, which would move all of this guidance behind the
cache boundary.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability, Capability, PrepareTools
from pydantic_ai.tools import SystemPromptFunc, Tool, ToolDefinition
from pydantic_ai.toolsets import AbstractToolset

from ..config import settings
from ..l10n import DEFAULT_LANGUAGE, Language, Localized, use_language
from ..prompts import (
    CITATION_INSTRUCTIONS,
    GROUNDING_INSTRUCTIONS,
    IMAGE_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS,
    PYTHON_INSTRUCTIONS,
    TMP_INSTRUCTIONS,
    VERSION_INSTRUCTIONS,
    WORKSPACE_PATH_INSTRUCTIONS,
    WRITE_INSTRUCTIONS,
)
from ..tools.pydantic_ai import invoke_tool
from ..types import (
    MODE_VALUES,
    MUTATING_MODES,
    Mode,
    ToolSchema,
    ToolsSpec,
)
from .app import turn_usage_limits
from .approval import ApprovalNotes
from .common import UserDeps
from .context import DOCUMENT_SCOPE, MODE_NOTE
from .tools import (
    INJECTABLE_TOOL_NAMES,
    SANDBOX_FUNCTION_NAMES,
    MemoryNote,
    ask_toolset,
    conversation_toolset,
    explore_toolset,
    memory_toolset,
    python_toolset,
    sandbox_instructions,
    subagent_toolset,
    web_toolset,
    write_toolset,
)

__all__ = [
    "FEATURES",
    "SHARED_INSTRUCTIONS",
    "Feature",
    "SharedInstructions",
    "agent_tool",
    "build_capabilities",
    "check_tool_settings",
    "collect_tool_schemas",
    "feature_tools",
    "invoke_agent_tool",
    "unlisted_tool_names",
]


type FeatureInstruction = Localized[str] | SystemPromptFunc[UserDeps]
"""A fixed block in every language, or a callable resolved per run."""


@dataclass(frozen=True, slots=True)
class Feature:
    """A named agent feature: a toolset, its instructions, its extras, and its modes.

    :meth:`capability` bundles the toolset and the instructions into the
    capability a run composes, once per language around the one shared
    toolset, and ``extras`` are the capabilities composed beside it.
    ``modes`` is the only selection metadata pydantic-ai has no concept of.
    ``tool_names`` is resolved once from the toolset, so the per-request
    disabled-tool check needs no toolset walk.
    """

    id: str
    toolset: FunctionToolset[UserDeps]
    tool_names: frozenset[str]
    instructions: Sequence[FeatureInstruction] = ()
    extras: Sequence[AbstractCapability[UserDeps]] = ()
    modes: frozenset[Mode] = MODE_VALUES

    @classmethod
    def build(
        cls,
        id: str,
        toolset: FunctionToolset[UserDeps],
        *,
        instructions: Sequence[FeatureInstruction] = (),
        extras: Sequence[AbstractCapability[UserDeps]] = (),
        modes: frozenset[Mode] = MODE_VALUES,
    ) -> Self:
        """Name a feature once, resolving its tool names from the toolset."""
        return cls(
            id=id,
            toolset=toolset,
            tool_names=frozenset(toolset.tools),
            instructions=instructions,
            extras=extras,
            modes=modes,
        )

    def capability(self, language: Language) -> AbstractCapability[UserDeps]:
        """The capability this feature contributes to a run in *language*."""
        return Capability(
            id=self.id,
            toolsets=[self.toolset],
            instructions=[
                part[language] if isinstance(part, Localized) else part
                for part in self.instructions
            ],
        )


# The single source of truth for the agent's features.  ``explore`` and
# ``memory`` carry their own instructions: ``explore`` states the grounding and
# version discipline for the retrieval tools it owns and appends the live
# document scope (so the model knows which documents the user selected), and
# ``memory`` explains editing and reports the stored memory when a
# conversation starts.  ``write`` is offered in every mode, since ``/tmp`` is writable in
# all of them, so its workspace guidance is a shared block narrowed to the modes
# that may write the workspaces.  The rest are bare toolset bundles.  Adding a
# feature here exposes it to the agent and the debug surface.
#
# Grounding rides here rather than on the agent so it appears exactly when the
# tools it mandates do: a user who disables every explore tool must not be left
# with a prompt ordering searches it can no longer perform.
FEATURES: tuple[Feature, ...] = (
    Feature.build(
        "explore",
        explore_toolset,
        instructions=[
            GROUNDING_INSTRUCTIONS,
            VERSION_INSTRUCTIONS,
            IMAGE_INSTRUCTIONS,
        ],
        extras=[DOCUMENT_SCOPE],
    ),
    Feature.build(
        "python",
        python_toolset,
        instructions=[PYTHON_INSTRUCTIONS, sandbox_instructions],
    ),
    Feature.build("subagent", subagent_toolset),
    Feature.build("write", write_toolset),
    Feature.build(
        "memory",
        memory_toolset,
        instructions=[MEMORY_INSTRUCTIONS],
        extras=[MemoryNote()],
        modes=MUTATING_MODES,
    ),
    Feature.build("web", web_toolset),
    Feature.build("conversation", conversation_toolset),
    Feature.build("ask", ask_toolset),
)


@dataclass(frozen=True, slots=True)
class SharedInstructions:
    """A prompt block owned by several features rather than by one.

    Some guidance describes what a group of features produces — the path syntax
    every document tool speaks, the citation markup for every source a tool can
    return — so it belongs to none of them alone and must not be repeated across
    them.  It is composed once when any of ``features`` is live and drops out
    when none is, the same rule a feature's own instructions follow, and
    ``modes`` narrows it further for a block whose subject a mode withholds.
    """

    id: str
    features: frozenset[str]
    text: Localized[str]
    modes: frozenset[Mode] = MODE_VALUES

    def capability(self, language: Language) -> AbstractCapability[UserDeps]:
        """The instructions-only capability this block contributes to a run."""
        return Capability(id=self.id, instructions=self.text[language])


SHARED_INSTRUCTIONS: tuple[SharedInstructions, ...] = (
    SharedInstructions(
        "workspace-paths",
        frozenset({"explore", "python", "write"}),
        WORKSPACE_PATH_INSTRUCTIONS,
    ),
    SharedInstructions(
        "citation", frozenset({"explore", "web"}), CITATION_INSTRUCTIONS
    ),
    SharedInstructions("tmp", frozenset({"python", "write"}), TMP_INSTRUCTIONS),
    SharedInstructions(
        "workspace-writes",
        frozenset({"write"}),
        WRITE_INSTRUCTIONS,
        modes=MUTATING_MODES,
    ),
)
"""Guidance spanning several features, composed while any of them is live."""


def _filter_unlisted(disabled: frozenset[str]) -> AbstractCapability[UserDeps]:
    """A capability that hides every disabled tool from the model, group-agnostic."""

    def prepare(
        _ctx: RunContext[UserDeps], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        return [td for td in tool_defs if td.name not in disabled]

    return PrepareTools(prepare, id="disabled-tools")


def unlisted_tool_names(tools_spec: ToolsSpec) -> frozenset[str]:
    """The tool names that reach the model with no schema.

    Three lists in one namespace: ``settings.tools.disabled`` is the operator's
    standing choice, ``ToolsSpec.disabled_tools`` the user's per-turn one, and
    ``settings.tools.sandbox_only`` the operator's placement choice.  The first
    two withhold a tool outright; the third only unlists it, since it is still
    injected as a function (``agents/tools/python.py``).

    One function, because the surfaces that must agree are the ones asking this
    question — the :class:`PrepareTools` pass and the settings listing.  What
    the sandbox withholds is a different question with a different answer, and
    it is asked where it is used (``agents.tools.python.sandbox_surface``)
    rather than published here as a near-twin of this name.
    """
    return frozenset(settings.tools.disabled).union(
        tools_spec.disabled_tools or (), settings.tools.sandbox_only
    )


def build_capabilities(
    tools_spec: ToolsSpec,
    *,
    extra: Sequence[AbstractToolset[UserDeps]] = (),
    mode: Mode,
    language: Language,
) -> Sequence[AbstractCapability[UserDeps]]:
    """Compose the capabilities for an agent run.

    Selects the features offered in ``mode``, drops any whose tools are all
    disabled (so a fully-disabled feature contributes neither tools nor
    instructions), adds the :data:`SHARED_INSTRUCTIONS` blocks whose features
    survived that selection, composes the live features' extras and the mode
    note, hides individually disabled tools via a single
    :class:`PrepareTools` capability, and wraps each extra toolset (e.g. an
    MCP server) as its own capability.

    A run withholds the union of two lists that differ only in who wrote them:
    ``settings.tools.disabled``, the operator's standing choice, and the
    request's own ``disabled_tools``, the user's per-turn one.  They are one
    namespace and one mechanism, so an operator exclusion drops a feature's
    instructions exactly as a user's does, and the :class:`PrepareTools` pass
    covers *extra* as well, which is the only reach an operator has over the
    tools a user-configured MCP server brings.

    The mode selects which features are offered at all (``read`` is handed
    none of the mutating ones), so the modes that may write compose the same
    prefix and differ only in the mode note.  Whether a write the remaining ones
    perform pauses for the user is decided per call by the gate in
    ``agents/tools/write.py``, which alone can see the path.

    Args:
        tools_spec: Combined tool configuration from the chat request.
        extra: Additional toolsets to expose (e.g. MCP servers).
        mode: Agent mode controlling which features are included.
        language: Language of every instruction, the tool schemas stay English.

    Returns:
        Sequence of capabilities ready to pass to the agent.
    """
    unlisted = unlisted_tool_names(tools_spec)

    features = [
        feature
        for feature in FEATURES
        if mode in feature.modes and not feature.tool_names <= unlisted
    ]
    live = {feature.id for feature in features}

    result: list[AbstractCapability[UserDeps]] = [
        feature.capability(language) for feature in features
    ]
    result.extend(
        shared.capability(language)
        for shared in SHARED_INSTRUCTIONS
        if shared.features & live and mode in shared.modes
    )

    result.extend(extra for feature in features for extra in feature.extras)
    result.append(MODE_NOTE)

    if unlisted:
        result.append(_filter_unlisted(unlisted))

    result.extend(Capability(toolsets=[toolset]) for toolset in extra)

    # Cross-cutting run-loop safeguards, applied to every run regardless of mode.
    # The output spill rides on the agents (`agents/app.py`).
    result.append(ApprovalNotes())

    return result


def check_tool_settings() -> None:
    """Refuse to start when the operator's tool lists name the wrong thing.

    A list entry that matches nothing withholds nothing, and the only symptom
    is a schema the operator believed was gone, so the typo has to surface at
    startup rather than on the first turn that pays for it.  Only the built-in
    tools can be checked: an MCP server's names are not known until its
    transport is opened, per user and per request.

    ``sandbox_only`` is held to the narrower set, since a name outside it would
    take a tool off the model's list and give it to no program either, which is
    an exclusion the operator did not ask for.  That the injectable set is
    itself registered needs no check: it is filtered from the lists that
    register these tools rather than kept beside them.

    ``disabled`` may also name a function only a program is handed, such as
    ``complete``, which withholds it like any tool.

    Raises:
        ValueError: If a name is not a built-in tool, or is not one the
            sandbox can be given.
    """
    known = {name for feature in FEATURES for name in feature.tool_names}
    known |= SANDBOX_FUNCTION_NAMES

    if unknown := sorted(set(settings.tools.disabled) - known):
        raise ValueError(
            f"tools.disabled names no such tool: {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(known))}"
        )

    if stranded := sorted(set(settings.tools.sandbox_only) - INJECTABLE_TOOL_NAMES):
        raise ValueError(
            f"tools.sandbox_only names no tool the model and run_python share: "
            f"{', '.join(stranded)}. Available: "
            f"{', '.join(sorted(INJECTABLE_TOOL_NAMES))}"
        )


def feature_tools() -> Iterator[tuple[str, Tool[UserDeps]]]:
    """Every agent tool with the id of the feature it belongs to."""
    return (
        (feature.id, tool)
        for feature in FEATURES
        for tool in feature.toolset.tools.values()
    )


def collect_tool_schemas() -> list[ToolSchema]:
    """Collect each tool's metadata and the JSON Schema of its parameters.

    Derived from the same :data:`FEATURES` the agent is composed from, grouped
    by feature id, so the listing matches what an agent can actually call.  The
    richer counterpart to a bare name/description listing: callers that only
    need :class:`ToolInfo` fields rely on the response model to drop
    ``parameters``.

    Returns:
        Flat list of tool schema entries.
    """
    return [
        ToolSchema(
            name=tool.name,
            description=tool.description or "",
            group=feature_id,
            parameters=tool.function_schema.json_schema,
        )
        for feature_id, tool in feature_tools()
    ]


def _feature_of(name: str) -> Feature:
    """The feature whose toolset holds the tool *name*.

    Raises:
        KeyError: If no tool named *name* is registered.
    """
    if feature := next((f for f in FEATURES if name in f.tool_names), None):
        return feature

    raise KeyError(name)


def agent_tool(name: str) -> Tool[UserDeps]:
    """The agent tool called *name*, looked up across every feature.

    Raises:
        KeyError: If no tool named *name* is registered.
    """
    return _feature_of(name).toolset.tools[name]


async def invoke_agent_tool(
    name: str,
    args: Mapping[str, Any],
    deps: UserDeps,
    *,
    approved: bool = False,
) -> Any:
    """Validate ``args`` and run the tool *name* with ``deps`` as one agent turn would.

    Runs the tool through the exact code path the agent uses (see
    :func:`hivegent.tools.pydantic_ai.invoke_tool`), its feature's toolset and
    so its timeout included, held to a turn's usage limits and in the default
    language like every agent tool call.

    Args:
        name: The agent tool to invoke.
        args: Raw argument mapping, validated against the tool's schema.
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).
        approved: Whether the user already approved this call.

    Returns:
        The raw return value, taken apart by
        :func:`hivegent.tools.pydantic_ai.unwrap_tool_output`.

    Raises:
        KeyError: If no tool named *name* is registered.
        pydantic.ValidationError: If ``args`` fail the tool's schema.
        ModelRetry: If the tool or its validator refuses the arguments.
        ApprovalRequired: If the write gate asks the user first.
    """
    toolset = _feature_of(name).toolset

    with use_language(DEFAULT_LANGUAGE):
        return await invoke_tool(
            toolset,
            name,
            args,
            deps,
            approved=approved,
            usage_limits=turn_usage_limits,
        )
