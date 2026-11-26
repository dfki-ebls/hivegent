"""Python agent tool registrations, the ``run_python`` feature.

The tool factory is settings-free, so this module is where the application
settings are applied to its parameters, and where the sandbox budget is
translated into the ``ResourceLimits`` the worker enforces.

It is also where the sandbox's own tool surface is decided, since that is a
property of the run rather than of the sandbox: which tools a program may call
is the same question as which tools the model may call, asked of the same two
lists.
"""

import asyncio
import os
from collections.abc import Mapping
from dataclasses import replace
from functools import cache

from cachetools import LRUCache
from exar.helpers import get_name
from exar.memory import digest
from exar.tools import Retry, effect_of, tool_action
from pydantic_ai import RunContext
from pydantic_ai.models import Model
from pydantic_monty import ResourceLimits

from ... import llm
from ...config import settings
from ...llm_config import turn_llm
from ...prompts import SANDBOX_TYPE_CHECK_INSTRUCTIONS, sandbox_api_instructions
from ...sandbox import get_monty_pool
from ...shared.l10n import Localized
from ...skills import skill_dir
from ...store import USER_PREFIX
from ...tools.base import ToolFunc
from ...tools.complete import COMPLETE_INSTRUCTIONS, CompletionReasoning, complete
from ...tools.monty import MontySurface, monty_surface
from ...tools.python import (
    CodeArg,
    PythonArgsArg,
    PythonScriptPathArg,
    ScriptCheck,
    check_script,
    run_python,
    validate_program_source,
)
from ...tools.workspace_os import MOUNT_STUB, WORKSPACE_MOUNT, ChangesetLimits
from ..common import (
    SELF_BOUNDED,
    AgentTools,
    CompletionRefused,
    Hooked,
    UserDeps,
    probe_context,
)
from .explore import INJECTED, injected_tools
from .write import program_writes

__all__ = [
    "INJECTABLE_TOOL_NAMES",
    "SANDBOX_FUNCTION_NAMES",
    "check_skill_script",
    "python_tools",
    "python_toolset",
    "sandbox_instructions",
    "validate_run_python",
]

_limits: ResourceLimits = {
    "max_feed_duration_secs": settings.sandbox.max_duration_seconds,
    "max_total_sleep_secs": settings.sandbox.max_duration_seconds,
    "max_memory": settings.sandbox.max_memory_bytes,
    "max_suspensions": settings.sandbox.max_suspensions,
}

_changeset_limits = ChangesetLimits(
    max_operations=settings.sandbox.max_changeset_operations,
    max_deletes=settings.sandbox.max_changeset_deletes,
    max_chars=settings.sandbox.max_changeset_chars,
)


# Handed the run rather than the deps, since each call counts on its usage.
# The model is built on a program's first call and reused by the rest, and the
# turn's completions on the deps bound the calls of all its programs.
def _complete(ctx: RunContext[UserDeps]) -> ToolFunc:
    config = turn_llm(ctx.deps.llm)
    completions = ctx.deps.completions
    capabilities = (completions,)

    @cache
    def model() -> Model:
        assert config is not None
        return llm.model_from_config(config)

    async def completion(prompt: str, reasoning: CompletionReasoning | None) -> str:
        if config is None:
            raise Retry("complete is unavailable: no model is configured")

        try:
            async with completions.slot():
                # The program never picks a model, only how hard it thinks.
                return await llm.complete(
                    [prompt],
                    replace(config, thinking=reasoning or False),
                    output_type=str,
                    timeout=settings.sandbox.completion_timeout_seconds,
                    answer_tokens=settings.sandbox.completion_max_tokens,
                    model=model(),
                    usage=ctx.usage,
                    usage_limits=ctx.usage_limits,
                    conversation_id=ctx.conversation_id,
                    capabilities=capabilities,
                )

        except CompletionRefused as exc:
            raise Retry(str(exc)) from exc

    return complete(completion=completion)


_GUIDANCE: Mapping[str, Localized[str]] = {"complete": COMPLETE_INSTRUCTIONS}
"""Guidance a run's prompt carries while a program may call the function of its name."""


def _sandbox_functions(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """Every function this deployment can hand a program of the run *ctx*."""
    return [*injected_tools(ctx), _complete(ctx)]


SANDBOX_FUNCTION_NAMES: frozenset[str] = frozenset({*INJECTED, "complete"})
"""Every name a program may be handed, which ``settings.tools.disabled`` may withhold."""

INJECTABLE_TOOL_NAMES: frozenset[str] = frozenset(INJECTED)
"""Every model tool a program can also be given, whether or not a run is.

The domain of ``settings.tools.sandbox_only``: naming anything else would
withhold a tool from the model and hand it to nobody, or move a function no
tool list carries in the first place, so the boot check refuses
it (:func:`~hivegent.agents.check_tool_settings`).
"""


def _live(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """The functions the policy of the run *ctx* allows outright, which only reads are.

    A running program cannot stop for an approval, so it is handed no tool the
    policy asks about.
    """
    policy = ctx.deps.policy

    return [
        func
        for func in _sandbox_functions(ctx)
        if (effect := effect_of(func)) == "read"
        and policy(tool_action(get_name(func), effect)) == "allow"
    ]


def _listed() -> frozenset[str]:
    """The functions the model also holds as tools, documented there.

    A withheld one is never rendered, so only ``sandbox_only`` is left to rule
    out.  It holds because every mode offering ``python`` offers the features
    these tools belong to.
    """
    return INJECTABLE_TOOL_NAMES.difference(settings.tools.sandbox_only)


def sandbox_surface(ctx: RunContext[UserDeps]) -> MontySurface:
    """The host functions and stub for the tools this run may call.

    Gated on exactly what gates the tool of the same name,
    :attr:`~hivegent.agents.common.UserDeps.policy`.  A tool
    withheld from the model's tool list must not reappear as a function, since
    the two are one namespace and injecting it would be the side door the
    exclusion exists to close.  A function only a program is handed answers to
    the same names.

    The mode gates nothing here, because nothing here writes: a run that may
    not touch the workspace is still free to search it.

    One builder for the prompt and for the call, so the stub the model was
    given and the stub the type checker enforces cannot come apart.
    """
    return monty_surface(_live(ctx), _listed())


def sandbox_instructions(ctx: RunContext[UserDeps]) -> str:
    """Declare the sandbox's tool surface, or say nothing when it has none.

    Composed per run rather than written once, since which functions exist is
    what the gate above decides, and a stub naming a function the program would
    get a ``NameError`` for is worse than no stub at all.  A function's own
    guidance (:data:`_GUIDANCE`) follows it in and out.

    ``declarations`` and not ``stubs``: the mount's ``open`` belongs to the type
    checker, and showing the model a declaration of a builtin it already knows
    would spend context saying nothing.  The prose is in the run's language,
    while the declarations stay English like every other tool schema.
    """
    surface = sandbox_surface(ctx)

    if not surface:
        return ""

    language = ctx.deps.language
    blocks = [sandbox_api_instructions(surface.declarations)[language]]
    blocks.extend(
        guidance[language]
        for name in surface.functions
        if (guidance := _GUIDANCE.get(name)) is not None
    )

    if settings.sandbox.type_check:
        blocks.append(SANDBOX_TYPE_CHECK_INSTRUCTIONS[language])

    return "".join(blocks)


# The worker pool comes from the lifespan, so the tool takes its getter.
# `paths` mounts the roots the read tools span, filters applied, and the
# skills, so a skill's script reads the files beside it, while `writable` is the
# narrower span its changes may touch, only `/tmp` in a mode that may not write,
# and `commit` lands them once it succeeded.
def _run_python(ctx: RunContext[UserDeps]) -> ToolFunc:
    deps = ctx.deps
    writes = program_writes(deps)

    return run_python(
        pool=get_monty_pool,
        limits=_limits,
        paths=deps.program_paths(),
        writable=writes.paths,
        commit=writes.commit,
        changeset_limits=_changeset_limits,
        surface=sandbox_surface(ctx),
        max_host_calls=settings.sandbox.max_host_calls,
        max_wall_seconds=settings.sandbox.max_wall_seconds,
        max_document_chars=settings.limits.max_file_size_bytes,
        type_check=settings.sandbox.type_check,
        environ=_environ(deps),
        script_environ=_script_environ,
    )


def _script_environ(script: str) -> Mapping[str, str]:
    """``SKILL_DIR`` for a script in a skill, the folder the host resolved it in."""
    folder = skill_dir(script)

    return {} if folder is None else {"SKILL_DIR": folder}


_CHECK_LIMITS: ResourceLimits = {
    "max_feed_duration_secs": 2.0,
    "max_total_sleep_secs": 0.0,
    "max_memory": 256_000_000,
}
"""A check runs definitions alone, which take no time worth waiting for."""

_checked: LRUCache[tuple[str, str], ScriptCheck] = LRUCache(maxsize=1024)
"""Every script checked, by its content and canonical path, never stored."""

_check_slots = asyncio.Semaphore(
    max(1, (settings.sandbox.max_processes or os.cpu_count() or 1) // 2)
)
"""The workers checks hold at once, at most half the pool, so ``run_python`` always finds one."""


@cache
def _program_stubs() -> str:
    """What any program is checked against: the mount and every function a run may be handed."""
    stubs = monty_surface(_sandbox_functions(probe_context()), _listed()).stubs

    return "\n\n".join(filter(None, (MOUNT_STUB, stubs)))


async def check_skill_script(source: str, script: str) -> ScriptCheck:
    """Whether the stored *script* runs by the ``main`` convention, from its content.

    Checked against every function a program could be handed, and cached by
    content, so a skill is checked once whenever it is listed or installed.
    """
    key = (digest(source), script)

    if (found := _checked.get(key)) is not None:
        return found

    async with _check_slots:
        checked = _checked[key] = await check_script(
            get_monty_pool(),
            source,
            script_name=script,
            stubs=_program_stubs(),
            environ=_script_environ(script),
            limits=_CHECK_LIMITS,
        )

    return checked


def _environ(deps: UserDeps) -> dict[str, str]:
    """Who runs the program, and that their workspace is its home, as a shell says it.

    Monty has no ``Path.home`` or ``expanduser``, so ``HOME`` is the one place
    the personal workspace has an absolute spelling, and it is the one a
    relative ``~/...`` already resolves to.  ``TMPDIR`` names ``/tmp`` where
    the conversation has one.
    """
    environ = {
        "HOME": str(WORKSPACE_MOUNT / USER_PREFIX),
        "USER": deps.user.id,
        "LOGNAME": deps.user.id,
    }

    if deps.tmp is not None:
        environ["TMPDIR"] = deps.tmp.mount.render("")

    return environ


async def validate_run_python(
    ctx: RunContext[UserDeps],
    code: CodeArg = None,
    script_path: PythonScriptPathArg = None,
    args: PythonArgsArg = None,
) -> None:
    """Reject invalid program sources before a worker is checked out for them.

    No approval is asked here: what a program changes is only known once it
    ran, so the user approves the staged changes through ``apply_changes``.
    """
    _ = validate_program_source(code, script_path, args)


def python_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc | Hooked]:
    """The ``run_python`` tool, for one run."""
    return [Hooked(_run_python(ctx), args_validator=validate_run_python)]


python_toolset = AgentTools(factory=python_tools, gateway=True, timeout=SELF_BOUNDED)
"""The sandbox, which bounds itself (``sandbox.max_wall_seconds``)."""
