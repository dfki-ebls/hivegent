"""Compute-oriented agent tool registrations.

The tool class is settings-free, so this module is where the application
settings are applied to its instance fields, and where the sandbox budget is
translated into the ``ResourceLimits`` the worker enforces.

It is also where the sandbox's own tool surface is decided, since that is a
property of the run rather than of the sandbox: which tools a program may call
is the same question as which tools the model may call, asked of the same two
lists.
"""

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_monty import ResourceLimits

from ...config import settings
from ...prompts import SANDBOX_TYPE_CHECK_INSTRUCTIONS, sandbox_api_instructions
from ...sandbox import get_monty_pool
from ...tools.base import (
    AsyncToolFactory,
    factory_tool_name,
    resolve_tool_cls,
    translate_tool_retry,
)
from ...tools.monty import MontySurface, monty_declarations, monty_surface
from ...tools.pydantic_ai import register_agent_tool
from ...tools.python import (
    CodeArg,
    PythonScriptPathArg,
    RunPythonTool,
    validate_program_source,
)
from ...tools.workspace_os import WORKSPACE_MOUNT, ChangesetLimits
from ..common import UserDeps
from .explore import EXPLORE_FACTORIES
from .web import WEB_FACTORIES
from .write import changeset_committer, program_paths

__all__ = [
    "INJECTABLE_TOOL_NAMES",
    "compute_toolset",
    "sandbox_instructions",
    "validate_run_python",
]

_limits: ResourceLimits = {
    "max_feed_duration_secs": settings.sandbox.max_duration_seconds,
    "max_total_sleep_secs": settings.sandbox.max_duration_seconds,
    "max_memory": settings.sandbox.max_memory_bytes,
}

_changeset_limits = ChangesetLimits(
    max_operations=settings.sandbox.max_changeset_operations,
    max_deletes=settings.sandbox.max_changeset_deletes,
    max_chars=settings.sandbox.max_changeset_chars,
)

# Filtered from the very lists that register these tools, rather than listed a
# second time: `Tool.injectable` says which of them a program may be handed,
# and it is a property of the tool, so a factory renamed or a feature switched
# off cannot leave the two out of step.  The web pair drops out because
# `WEB_FACTORIES` is already empty when the operator's switch is.
_INJECTABLE_FACTORIES: tuple[AsyncToolFactory[UserDeps], ...] = tuple(
    factory
    for factory in (*EXPLORE_FACTORIES, *WEB_FACTORIES)
    if resolve_tool_cls(factory).injectable
)
"""The tools this deployment can hand a program, in registration order."""

INJECTABLE_TOOL_NAMES: frozenset[str] = frozenset(
    map(factory_tool_name, _INJECTABLE_FACTORIES)
)
"""Every tool a program can be given, whether or not a given run is given it.

The domain of ``settings.tools.sandbox_only``: naming anything else would
withhold a tool from the model and hand it to nobody, so the boot check refuses
it (:func:`~hivegent.agents.check_tool_settings`).  That each of these is a
registered tool needs no check, since the set is derived from what registers
them.
"""


def _sandbox_factories(deps: UserDeps) -> tuple[AsyncToolFactory[UserDeps], ...]:
    """Return the injected factories that are live for this run."""
    return tuple(
        factory
        for factory in _INJECTABLE_FACTORIES
        if factory_tool_name(factory) not in deps.withheld_tools
    )


def sandbox_surface(deps: UserDeps) -> MontySurface:
    """Build the host functions and stub for the tools this run may call.

    Gated on exactly what gates the tool of the same name: ``web_enabled`` has
    already dropped the web pair from :data:`_INJECTABLE_FACTORIES`, and
    :attr:`~hivegent.agents.common.UserDeps.withheld_tools` joins the
    operator's ``settings.tools.disabled`` to the request's own.  A tool withheld from the model's
    tool list must not reappear as a function, since the two are one namespace
    and injecting it would be the side door the exclusion exists to close.

    The mode gates nothing here, because nothing here writes: a run that may
    not touch the workspace is still free to search it.

    One builder for the prompt and for the call, so the stub the model was
    given and the stub the type checker enforces cannot come apart.
    """
    return monty_surface(_sandbox_factories(deps), deps)


def sandbox_instructions(ctx: RunContext[UserDeps]) -> str:
    """Declare the sandbox's tool surface, or say nothing when it has none.

    Composed per run rather than written once, since which functions exist is
    what the gate above decides, and a stub naming a function the program would
    get a ``NameError`` for is worse than no stub at all.

    ``declarations`` and not ``stubs``: the mount's ``open`` belongs to the type
    checker, and showing the model a declaration of a builtin it already knows
    would spend context saying nothing.  The prose is in the run's language,
    while the declarations stay English like every other tool schema.
    """
    declarations = monty_declarations(_sandbox_factories(ctx.deps))

    if not declarations:
        return ""

    language = ctx.deps.language
    declared = sandbox_api_instructions(declarations)[language]

    if not settings.sandbox.type_check:
        return declared

    return declared + SANDBOX_TYPE_CHECK_INSTRUCTIONS[language]


# A factory runs per tool call, so it only wires up fields.  The worker pool
# comes from the lifespan.  `paths` mounts the same roots the read tools span,
# filters applied, so a program reaches what the read tools reach and no more,
# while `writable` is the narrower span its changes may touch, only `/tmp` in a
# mode that may not write, and `commit` lands them once it succeeded.
def _run_python(deps: UserDeps) -> RunPythonTool:
    return RunPythonTool(
        pool=get_monty_pool(),
        limits=_limits,
        paths=deps.search_paths(),
        writable=program_paths(deps),
        commit=changeset_committer(deps),
        changeset_limits=_changeset_limits,
        surface=sandbox_surface(deps),
        type_check=settings.sandbox.type_check,
        environ=_environ(deps),
    )


def _environ(deps: UserDeps) -> dict[str, str]:
    """Who runs the program, and that their workspace is its home, as a shell says it.

    Monty has no ``Path.home`` or ``expanduser``, so ``HOME`` is the one place
    the personal workspace has an absolute spelling, and it is the one a
    relative ``~/...`` already resolves to.  ``TMPDIR`` names ``/tmp`` where
    the conversation has one.
    """
    environ = {
        "HOME": str(WORKSPACE_MOUNT / deps.store.scope.prefix),
        "USER": deps.user_id,
        "LOGNAME": deps.user_id,
    }

    if deps.tmp is not None:
        environ["TMPDIR"] = deps.tmp.prefixed("")

    return environ


compute_toolset: FunctionToolset[UserDeps] = FunctionToolset()


def validate_run_python(
    ctx: RunContext[UserDeps],
    code: CodeArg = None,
    script_path: PythonScriptPathArg = None,
) -> None:
    """Reject invalid program sources before a worker is checked out for them.

    No approval is asked here: what a program changes is only known once it
    ran, so the user approves the staged changes through ``apply_changes``.
    """
    with translate_tool_retry(ModelRetry):
        _ = validate_program_source(code, script_path)


register_agent_tool(
    compute_toolset,
    UserDeps,
    _run_python,
    args_validator=validate_run_python,
)
