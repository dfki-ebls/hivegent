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
from pydantic_monty import ResourceLimits

from ...config import settings
from ...prompts import SANDBOX_API_INSTRUCTIONS, SANDBOX_TYPE_CHECK_INSTRUCTION
from ...sandbox import get_monty_pool
from ...tools.base import factory_tool_name, resolve_tool_cls
from ...tools.monty import MontySurface, monty_surface
from ...tools.pydantic_ai import register_agent_tool
from ...tools.python import RunPythonTool
from ..common import UserDeps
from .explore import EXPLORE_FACTORIES
from .web import WEB_FACTORIES
from .write import output_sink, validate_commit_path

__all__ = ["INJECTABLE_TOOL_NAMES", "compute_toolset", "sandbox_api_instructions"]

_limits: ResourceLimits = {
    "max_duration_secs": settings.sandbox.max_duration_seconds,
    "max_memory": settings.sandbox.max_memory_bytes,
}

# Filtered from the very lists that register these tools, rather than listed a
# second time: `Tool.injectable` says which of them a program may be handed,
# and it is a property of the tool, so a factory renamed or a feature switched
# off cannot leave the two out of step.  The web pair drops out because
# `WEB_FACTORIES` is already empty when the operator's switch is.
_INJECTABLE_FACTORIES = tuple(
    factory
    for factory in (*EXPLORE_FACTORIES, *WEB_FACTORIES)
    if resolve_tool_cls(factory).injectable
)
"""The tools this deployment can hand a program, in registration order."""

INJECTABLE_TOOL_NAMES: frozenset[str] = frozenset(map(factory_tool_name, _INJECTABLE_FACTORIES))
"""Every tool a program can be given, whether or not a given run is given it.

The domain of ``settings.tools.sandbox_only``: naming anything else would
withhold a tool from the model and hand it to nobody, so the boot check refuses
it (:func:`~hivegent.agents.check_tool_settings`).  That each of these is a
registered tool needs no check, since the set is derived from what registers
them.
"""


def sandbox_surface(deps: UserDeps) -> MontySurface:
    """Build the host functions and stub for the tools this run may call.

    Gated on exactly what gates the tool of the same name: ``web_enabled`` has
    already dropped the web pair from :data:`_INJECTABLE_FACTORIES`, and
    ``settings.tools.disabled`` is unioned with the request's own withheld
    names here rather than read off ``deps``.  A tool withheld from the model's
    tool list must not reappear as a function, since the two are one namespace
    and injecting it would be the side door the exclusion exists to close, and
    reading the operator's list here rather than trusting a deps field means a
    construction site that never set one still cannot open that door.

    The mode gates nothing here, because nothing here writes: a run that may
    not touch the workspace is still free to search it.

    One builder for the prompt and for the call, so the stub the model was
    given and the stub the type checker enforces cannot come apart.
    """
    withheld = deps.disabled_tools.union(settings.tools.disabled)

    return monty_surface(
        [f for f in _INJECTABLE_FACTORIES if factory_tool_name(f) not in withheld], deps
    )


def sandbox_api_instructions(ctx: RunContext[UserDeps]) -> str:
    """Declare the sandbox's tool surface, or say nothing when it has none.

    Composed per run rather than written once, since which functions exist is
    what the gate above decides, and a stub naming a function the program would
    get a ``NameError`` for is worse than no stub at all.

    ``declarations`` and not ``stubs``: the mount's ``open`` belongs to the type
    checker, and showing the model a declaration of a builtin it already knows
    would spend context saying nothing.
    """
    surface = sandbox_surface(ctx.deps)

    if not surface:
        return ""

    declared = SANDBOX_API_INSTRUCTIONS.format(declarations=surface.declarations)

    if not settings.sandbox.type_check:
        return declared

    return declared + SANDBOX_TYPE_CHECK_INSTRUCTION


# A factory runs per tool call, so it only wires up fields.  The worker pool
# comes from the lifespan.  `paths` mounts the same roots the read tools span,
# filters applied, so a program reaches what the read tools reach and no more,
# while the writer both commits the declared output and names the narrower span
# whose `.scratch/` state a program may write in place.
def _run_python(deps: UserDeps) -> RunPythonTool:
    return RunPythonTool(
        pool=get_monty_pool(),
        limits=_limits,
        paths=deps.search_paths(),
        writer=output_sink(deps),
        surface=sandbox_surface(deps),
        type_check=settings.sandbox.type_check,
    )


compute_toolset: FunctionToolset[UserDeps] = FunctionToolset()

# The sandbox names its destination rather than capturing a result, so it takes
# the gate alone and not the redirect's suffix check — but the same gate, since
# both persist a document on the model's say-so.
register_agent_tool(
    compute_toolset,
    UserDeps,
    _run_python,
    args_validator=validate_commit_path,
)
