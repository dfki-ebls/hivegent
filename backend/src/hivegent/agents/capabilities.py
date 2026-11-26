"""Capability composition and the agent feature registry.

A capability is the pydantic-ai v2 primitive that bundles a feature's toolset
with the instructions, hooks, and model settings that belong to it, so a whole
feature reaches the agent through one composable unit.

This module is the single source of truth for the agent's features:
:data:`FEATURES` lists each one as a :class:`Feature` (a capability whose tools
each declare their traits), the built-in ones and every enabled plugin's, and
every consumer derives from it.  A mode offers
a feature whole or not at all, by what its tools declare (:mod:`.policy`). The agent run
composes from the capabilities, while the debug/meta REST surface lists and
invokes individual tools extracted from the very same capabilities, so an admin
inspects exactly the tools an agent is built from.

Guidance follows the tools it describes: a block bound to one feature rides on
its capability, a block spanning several is a :class:`SharedInstructions` entry
composed as soon as any of them is live, and only behaviour tied to no
feature at all (base prompt, language, math) stays agent-level ``instructions``.
State that changes during a conversation is an ``on_change='append'``
instruction (:mod:`.context`), whose changes extend the history instead of
rewriting the prefix.
Prompt text that explains a tool the model was not given is a defect, so nothing
that names a tool belongs in the agent-level set.

Instructions are composed in the run's interface language, chosen explicitly
rather than from the ambient request, while the toolsets and their schemas are
one English set shared by every language.  A feature therefore holds its fixed
instructions as :class:`~hivegent.shared.l10n.Localized` text and resolves them into
plain strings when it builds the run's capability: an instruction callable
reading the language off the run would do the same with less, but pydantic-ai
marks every callable dynamic, which would move all of this guidance behind the
cache boundary.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from exar.agents import session
from exar.helpers import get_name
from exar.pydantic_ai import Render, RunNote, definition_action
from exar.pydantic_ai.guards import LenientArguments
from exar.tools import effect_of
from pydantic_ai import FunctionToolset
from pydantic_ai.capabilities import (
    AbstractCapability,
    Capability,
    CombinedCapability,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import ToolDefinition

from ..config import settings
from ..plugins import api
from ..plugins.loader import merge
from ..prompts import (
    CITATION_INSTRUCTIONS,
    GROUNDING_INSTRUCTIONS,
    IMAGE_INSTRUCTIONS,
    MEMORY_INSTRUCTIONS,
    PYTHON_INSTRUCTIONS,
    SKILLS_INSTRUCTIONS,
    TMP_INSTRUCTIONS,
    VERSION_INSTRUCTIONS,
    WORKSPACE_PATH_INSTRUCTIONS,
    WRITE_INSTRUCTIONS,
)
from ..shared.l10n import DEFAULT_LANGUAGE, Language, Localized, use_language
from ..types import (
    MODE_VALUES,
    WRITABLE_MODES,
    Mode,
    ToolSchema,
)
from .app import turn_usage_limits
from .approval import ApprovalNotes
from .common import AgentTools, UserDeps, run_context, static_tools
from .context import (
    DATE_NOTE,
    DOCUMENT_SCOPE,
    MODE_NOTE,
    PERSONAL_INSTRUCTIONS,
)
from .guards import HOST_GATE, SPILL_TOOLS
from .integrations import is_integration_tool
from .policy import host_policy, listed
from .subagents import DELEGATION_TOOLS, SUBAGENTS
from .tools import (
    INJECTABLE_TOOL_NAMES,
    MEMORY_NOTE,
    SANDBOX_FUNCTION_NAMES,
    SKILLS_NOTE,
    ask_toolset,
    cases_toolset,
    conversation_toolset,
    explore_toolset,
    memory_toolset,
    python_toolset,
    sandbox_instructions,
    skills_toolset,
    web_toolset,
    write_toolset,
)

__all__ = [
    "FEATURES",
    "KNOWN_TOOL_NAMES",
    "PLUGIN_FEATURE_IDS",
    "SHARED_INSTRUCTIONS",
    "TOOL_NAMES",
    "Feature",
    "SharedInstructions",
    "build_capabilities",
    "check_tool_settings",
    "collect_tool_schemas",
    "feature_tools",
    "invoke_agent_tool",
    "unknown_tool_names",
]


@dataclass(frozen=True, slots=True)
class Feature:
    """A named agent feature: its tools, their instructions, its extras, and its modes.

    :meth:`capability` bundles the tools and the fixed instructions into the
    capability a run composes, once per language around the tools built for
    the run, ``rendered`` are the instructions rendered once per run as
    fixed text (a ``RunNote`` with ``on_change='rewrite'``), and ``extras``
    are the capabilities composed beside them.
    ``tool_defs`` are resolved once from the tools, so the per-request
    offering check needs no build, and ``modes`` from what they declare:
    a mode the host policy denies any of them withholds the feature, so no
    instruction ever explains a tool the mode took away.
    """

    id: str
    tools: AgentTools
    tool_defs: tuple[ToolDefinition, ...]
    modes: frozenset[Mode]
    instructions: Sequence[Localized[str]] = ()
    rendered: Sequence[Render[UserDeps]] = ()
    extras: Sequence[AbstractCapability[UserDeps]] = ()

    @classmethod
    def build(
        cls,
        id: str,
        tools: AgentTools,
        *,
        instructions: Sequence[Localized[str]] = (),
        rendered: Sequence[Render[UserDeps]] = (),
        extras: Sequence[AbstractCapability[UserDeps]] = (),
    ) -> Self:
        """Name a feature once, resolving its tool definitions and modes from its tools."""
        tool_defs = static_tools(tools)
        actions = [definition_action(tool_def) for tool_def in tool_defs]

        return cls(
            id=id,
            tools=tools,
            tool_defs=tool_defs,
            modes=frozenset(
                mode
                for mode in MODE_VALUES
                if all(host_policy(mode)(action) != "deny" for action in actions)
            ),
            instructions=instructions,
            rendered=rendered,
            extras=extras,
        )

    @property
    def tool_names(self) -> frozenset[str]:
        """The names of its tools."""
        return frozenset(tool_def.name for tool_def in self.tool_defs)

    def live(self, deps: UserDeps) -> bool:
        """Whether a run over *deps* composes it: its mode offers it, and its policy lists one of its tools."""
        return deps.mode in self.modes and any(
            listed(deps.policy, definition_action(tool_def))
            for tool_def in self.tool_defs
        )

    def capability(self, language: Language) -> AbstractCapability[UserDeps]:
        """The tools and the fixed instructions this feature contributes to a run in *language*."""
        return Capability(
            id=self.id,
            toolsets=[self.tools.toolset()],
            instructions=[part[language] for part in self.instructions],
        )


# The single source of truth for the agent's features.  ``explore`` and
# ``memory`` carry their own instructions: ``explore`` states the grounding and
# version discipline for the retrieval tools it owns and appends the live
# document scope (so the model knows which documents the user selected), and
# ``memory`` explains editing and reports the stored memory when a
# conversation starts.  ``write`` is offered in every mode, since its tools
# write through the gateway and ``/tmp`` is writable in all of them, so its
# workspace guidance is a shared block narrowed to the modes that may write the
# workspaces.  The rest are bare toolset bundles.  Adding a
# feature here exposes it to the agent and the debug surface.
#
# Grounding rides here rather than on the agent so it appears exactly when the
# tools it mandates do: a user who disables every explore tool must not be left
# with a prompt ordering searches it can no longer perform.
_BUILT_IN: tuple[Feature, ...] = (
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
        instructions=[PYTHON_INSTRUCTIONS],
        rendered=[sandbox_instructions],
    ),
    Feature.build("write", write_toolset),
    Feature.build("cases", cases_toolset),
    Feature.build(
        "memory",
        memory_toolset,
        instructions=[MEMORY_INSTRUCTIONS],
        extras=[MEMORY_NOTE],
    ),
    Feature.build(
        "skills",
        skills_toolset,
        instructions=[SKILLS_INSTRUCTIONS],
        extras=[SKILLS_NOTE],
    ),
    Feature.build("web", web_toolset),
    Feature.build("conversation", conversation_toolset),
    Feature.build("ask", ask_toolset),
)


def _plugin_feature(plugin: str, feature: api.Feature) -> Feature:
    """The agent feature a plugin contributes, held to the host's policy.

    Each tool must declare its effect with `exar.tools.effect`, which decides
    its modes and approvals.  None writes through the changeset gateway,
    whose writes are approved per item by validators only built-in tools carry.

    Raises:
        ValueError: For a tool declaring no effect.
    """
    tools = tuple(feature.tools)

    for func in tools:
        if getattr(func, "effect", None) is None:
            raise ValueError(
                f"Plugin {plugin!r} tool {get_name(func)!r} declares no Effect"
            )

        _ = effect_of(func)

    return Feature.build(
        feature.id,
        AgentTools(factory=lambda _ctx: tools),
        instructions=() if feature.instructions is None else (feature.instructions,),
    )


_FEATURES = merge(
    "feature",
    ((feature.id, feature) for feature in _BUILT_IN),
    lambda loaded: (
        (feature.id, _plugin_feature(loaded.name, feature))
        for feature in loaded.plugin.features
    ),
)

FEATURES: tuple[Feature, ...] = tuple(_FEATURES.values())
"""The built-in features and every enabled plugin's."""

PLUGIN_FEATURE_IDS = frozenset(_FEATURES.keys() - {feature.id for feature in _BUILT_IN})
"""The features the enabled plugins contribute."""

TOOL_NAMES = frozenset(
    merge(
        "tool",
        ((name, feature.id) for feature in _BUILT_IN for name in feature.tool_names),
        lambda loaded: (
            (get_name(tool), feature.id)
            for feature in loaded.plugin.features
            for tool in feature.tools
        ),
    )
)
"""Every agent tool any feature registers, none of them twice."""


@dataclass(frozen=True, slots=True)
class SharedInstructions:
    """A prompt block owned by several features rather than by one.

    Some guidance describes what a group of features produces (the path syntax
    every document tool speaks, the citation markup for every source a tool can
    return), so it belongs to none of them alone and must not be repeated across
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
        modes=WRITABLE_MODES,
    ),
)
"""Guidance spanning several features, composed while any of them is live."""


def build_capabilities(
    deps: UserDeps, *, extra: Sequence[AbstractCapability[UserDeps]] = ()
) -> Sequence[AbstractCapability[UserDeps]]:
    """Compose the capabilities for an agent run over *deps*.

    Selects the features *deps* compose (:meth:`Feature.live`), so a feature
    whose every tool is withheld contributes neither tools nor instructions,
    adds the :data:`SHARED_INSTRUCTIONS` blocks whose features survived that
    selection, the instructions the features render for the run, the *extra*
    capabilities, and last the run notes, which differ between conversations
    (see "Interface language" in the README for the order).

    Which tools a run is offered is decided by its deps alone
    (``UserDeps.policy``), which :data:`~.guards.HOST_GATE` applies to every
    tool, the extra ones and the subagents' included, on every agent,
    by what it declares and by its name.

    The mode selects which features are offered at all (``Feature.modes``,
    derived from what their tools declare), so the modes that may write compose
    the same prefix and differ only in the mode note.  Whether a call of the
    remaining ones pauses for the user is the host policy's (``agents/policy.py``):
    a gateway write is decided per item by the validators in
    ``agents/tools/write.py``, which alone can see the path, and every other
    call by the policy, as an approval :data:`~.guards.HOST_GATE` requires.

    Args:
        deps: The run's deps, whose mode, language, and withheld tools decide.
        extra: Additional capabilities whose tools declare their effects,
            such as the integrations the user sees.

    Returns:
        Sequence of capabilities ready to pass to the agent.
    """
    mode, language = deps.mode, deps.language
    features = [feature for feature in FEATURES if feature.live(deps)]
    live = {feature.id for feature in features}

    result: list[AbstractCapability[UserDeps]] = [
        feature.capability(language) for feature in features
    ]
    result.extend(
        shared.capability(language)
        for shared in SHARED_INSTRUCTIONS
        if shared.features & live and mode in shared.modes
    )

    result.extend(
        RunNote[UserDeps](render=render, on_change="rewrite")
        for feature in features
        for render in feature.rendered
    )
    result.extend(extra)
    result.extend(note for feature in features for note in feature.extras)
    result.extend([DATE_NOTE, MODE_NOTE, PERSONAL_INSTRUCTIONS])

    # Cross-cutting run-loop safeguards, applied to every run regardless of mode.
    # The output spill rides on the agents (`agents/app.py`).
    result.append(ApprovalNotes())

    return result


_SUBAGENT_NAMES = frozenset(SUBAGENTS) if settings.subagents.enabled else frozenset()
_DELEGATION_NAMES = (
    frozenset(DELEGATION_TOOLS) if settings.subagents.enabled else frozenset()
)

KNOWN_TOOL_NAMES = (
    TOOL_NAMES | SANDBOX_FUNCTION_NAMES | _DELEGATION_NAMES | _SUBAGENT_NAMES
)
"""Every name a list of withheld tools may hold.

The agent tools, the program-only functions, and while subagents are enabled
the delegation tools and the subagents.
"""


def unknown_tool_names(names: Iterable[str]) -> list[str]:
    """The sorted names among *names* that withhold nothing, since no tool has them.

    A name an external server's id prefixes is taken, whether or not the
    server is up to list it.
    """
    return sorted(
        name for name in set(names) - KNOWN_TOOL_NAMES if not is_integration_tool(name)
    )


def check_tool_settings() -> None:
    """Refuse to start when the operator's tool lists name the wrong thing.

    A list entry that matches nothing withholds nothing, and the only symptom
    is a schema the operator believed was gone, so the typo has to surface at
    startup rather than on the first turn that pays for it.

    ``sandbox_only`` is held to the narrower set, since a name outside it would
    take a tool off the model's list and give it to no program either, which is
    an exclusion the operator did not ask for.  That the injected set is
    itself registered needs no check: it is taken by name from the list that
    builds these tools rather than kept beside them.

    ``disabled`` may also name a function only a program is handed, such as
    ``complete``, which withholds it like any tool.

    The id of an external server, database, or API must prefix no built-in tool,
    the harness's reader of saved results, and the delegation tools
    included, whether or not subagents are enabled, or one of its tools could take that tool's name.


    Raises:
        ValueError: If a name is not a built-in tool, or is not one the
            sandbox can be given, or a server, database, or API id prefixes a built-in tool.
    """
    taken = KNOWN_TOOL_NAMES | DELEGATION_TOOLS.keys() | SPILL_TOOLS.keys()

    if clashes := sorted(name for name in taken if is_integration_tool(name)):
        raise ValueError(
            f"mcp_servers, databases, and apis ids prefix the built-in tools {', '.join(clashes)}, "
            "rename those servers"
        )

    if unknown := unknown_tool_names(settings.tools.disabled):
        raise ValueError(
            f"tools.disabled names no such tool: {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(KNOWN_TOOL_NAMES))}"
        )

    if unknown := unknown_tool_names(settings.tools.budgets):
        raise ValueError(
            f"tools.budgets names no such tool: {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(KNOWN_TOOL_NAMES))}"
        )

    if stranded := sorted(set(settings.tools.sandbox_only) - INJECTABLE_TOOL_NAMES):
        raise ValueError(
            f"tools.sandbox_only names no tool the model and run_python share: "
            f"{', '.join(stranded)}. Available: "
            f"{', '.join(sorted(INJECTABLE_TOOL_NAMES))}"
        )


def feature_tools() -> Iterator[tuple[str, ToolDefinition]]:
    """The definition of every agent tool with the id of the feature it belongs to."""
    return (
        (feature.id, tool_def) for feature in FEATURES for tool_def in feature.tool_defs
    )


def collect_tool_schemas() -> list[ToolSchema]:
    """Collect each tool's metadata and the JSON Schema of its parameters.

    Derived from the same :data:`FEATURES` the agent is composed from, grouped
    by feature id, so the listing matches what an agent can actually call.

    Returns:
        Flat list of tool schema entries.
    """
    return [
        ToolSchema(
            name=tool_def.name,
            description=tool_def.description or "",
            group=feature_id,
            parameters=tool_def.parameters_json_schema,
        )
        for feature_id, tool_def in feature_tools()
    ]


def _feature_of(name: str) -> Feature:
    """The feature whose toolset holds the tool *name*.

    Raises:
        KeyError: If no tool named *name* is registered.
    """
    if feature := next((f for f in FEATURES if name in f.tool_names), None):
        return feature

    raise KeyError(name)


async def invoke_agent_tool(
    name: str,
    args: Mapping[str, Any],
    deps: UserDeps,
    *,
    approved: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> Any:
    """Validate ``args`` and run the tool *name* with ``deps`` as one agent turn would.

    Builds the tool's feature for a run over *deps*, held through :data:`~.guards.HOST_GATE`
    to the host policy of the run in an exar run of its own (``UserDeps.run_config``),
    and calls it through pydantic-ai's own :class:`ToolManager` over a toolset
    of the tool alone, so its ``prepare`` hook, its schema, its validator, its
    approval, and its timeout apply exactly as in a run, with errors raised
    rather than turned into retry prompts.  Held to a turn's usage limits and
    in the default language like every agent tool call.

    Args:
        name: The agent tool to invoke.
        args: Raw argument mapping, validated against the tool's schema.
        deps: The dependencies passed to the tool (e.g. ``UserDeps``).
        approved: Whether the user already approved this call.
        metadata: What the approval carries, as ``DeferredToolResults.metadata``
            does, such as the changes it is bound to (``approval.APPROVED_CHANGES``).

    Returns:
        The raw return value, taken apart by
        ``exar.tools.batch.unpack``.

    Raises:
        KeyError: If no tool named *name* is registered, or the mode or *deps* withhold it.
        pydantic.ValidationError: If ``args`` fail the tool's schema.
        ModelRetry: If the tool or its validator refuses the arguments, or
            its ``prepare`` hook withholds it.
        ApprovalRequired: If the policy or the write gate asks the user first.
    """
    feature = _feature_of(name)
    context = run_context(deps, usage_limits=turn_usage_limits)
    toolset = feature.tools.build(context)
    tool = toolset.tools.get(name)

    async with session(dict(args), deps.run_config, name=name, record=False):
        if tool is None or not await HOST_GATE.prepare_tools(context, [tool.tool_def]):
            raise KeyError(name)

        alone = FunctionToolset[UserDeps](
            [tool], max_retries=toolset.max_retries, timeout=toolset.timeout
        )
        root = CombinedCapability[UserDeps]([LenientArguments(), HOST_GATE])

        with use_language(DEFAULT_LANGUAGE):
            manager = await ToolManager(alone, root_capability=root).for_run_step(
                context
            )

            return await manager.handle_call(
                ToolCallPart(name, dict(args)),
                approved=approved,
                metadata=None if metadata is None else dict(metadata),
                wrap_validation_errors=False,
            )
