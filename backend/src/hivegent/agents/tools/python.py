"""Python agent tool registrations, the ``run_python`` feature.

The tool class is settings-free, so this module is where the application
settings are applied to its instance fields, and where the sandbox budget is
translated into the ``ResourceLimits`` the worker enforces.

It is also where the sandbox's own tool surface is decided, since that is a
property of the run rather than of the sandbox: which tools a program may call
is the same question as which tools the model may call, asked of the same two
lists.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Any

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models import Model
from pydantic_monty import ResourceLimits

from ... import llm
from ...config import settings
from ...llm_config import LlmConfig, resolve_llm_config
from ...prompts import SANDBOX_TYPE_CHECK_INSTRUCTIONS, sandbox_api_instructions
from ...sandbox import get_monty_pool
from ...tools.base import (
    AsyncTool,
    Tool,
    ToolRetry,
    factory_tool_name,
    resolve_tool_cls,
    translate_tool_retry,
)
from ...tools.complete import CompleteTool
from ...tools.monty import MontySurface, monty_declarations, monty_surface
from ...tools.pydantic_ai import register_agent_tool
from ...tools.python import (
    CodeArg,
    PythonScriptPathArg,
    RunPythonTool,
    validate_program_source,
)
from ...tools.workspace_os import WORKSPACE_MOUNT, ChangesetLimits
from ..common import CompletionRefused, UserDeps
from .explore import EXPLORE_FACTORIES
from .web import WEB_FACTORIES
from .write import changeset_committer, program_paths

__all__ = [
    "INJECTABLE_TOOL_NAMES",
    "SANDBOX_FUNCTION_NAMES",
    "python_toolset",
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


def _completion_config(deps: UserDeps) -> LlmConfig:
    """The aux tier the request resolved, capped to one call's output.

    The aux model is the one an operator sizes for many small one-shot calls,
    which is exactly what a program's loop makes, and it falls back to the main
    model where none is set.  The program never picks one.
    """
    config = deps.aux_llm or resolve_llm_config(LlmConfig())
    max_tokens = llm.capped_tokens(config, settings.sandbox.completion_max_tokens)

    return config.model_copy(update={"max_tokens": max_tokens})


# Handed the run rather than the deps, since each call counts on its usage.
# The model is built on a program's first call and reused by the rest, and the
# turn's completions on the deps bound the calls of all its programs.
def _complete(ctx: RunContext[UserDeps]) -> CompleteTool:
    config = _completion_config(ctx.deps)
    completions = ctx.deps.completions
    capabilities = (completions,)

    @cache
    def model() -> Model:
        return llm.model_from_config(config)

    async def completion(prompt: str) -> str:
        try:
            async with completions.slot():
                return await llm.complete(
                    [prompt],
                    config,
                    model=model(),
                    timeout=settings.sandbox.completion_timeout_seconds,
                    usage=ctx.usage,
                    usage_limits=ctx.usage_limits,
                    conversation_id=ctx.conversation_id,
                    capabilities=capabilities,
                )

        except CompletionRefused as exc:
            raise ToolRetry(str(exc)) from exc

    return CompleteTool(completion=completion)


@dataclass(frozen=True, slots=True)
class _SandboxFunction:
    """A function a program may be handed, named and described by its factory."""

    factory: Callable[..., AsyncTool[Any]]
    takes_ctx: bool = False
    """Whether the factory is handed the run rather than its deps."""

    @property
    def name(self) -> str:
        """The function's name, which is also its tool's."""
        return factory_tool_name(self.factory)

    @property
    def tool_cls(self) -> type[Tool[Any]]:
        """The tool class, which declares how the function is offered."""
        return resolve_tool_cls(self.factory)

    def build(self, ctx: RunContext[UserDeps]) -> AsyncTool[Any]:
        """The tool for one program of the run *ctx*."""
        return self.factory(ctx if self.takes_ctx else ctx.deps)


# Filtered from the very lists that register the model's tools, rather than
# listed a second time, so a factory renamed or a feature switched off cannot
# leave the two out of step, and the web pair drops out because
# `WEB_FACTORIES` is already empty when the operator's switch is.
# `Tool.injectable` says which of them a program may be handed.
_SANDBOX_FUNCTIONS: tuple[_SandboxFunction, ...] = tuple(
    function
    for function in (
        *map(_SandboxFunction, (*EXPLORE_FACTORIES, *WEB_FACTORIES)),
        _SandboxFunction(_complete, takes_ctx=True),
    )
    if function.tool_cls.injectable
)
"""Every function this deployment can hand a program, in registration order."""

SANDBOX_FUNCTION_NAMES: frozenset[str] = frozenset(
    function.name for function in _SANDBOX_FUNCTIONS
)
"""Every name a program may be handed, which ``settings.tools.disabled`` may withhold."""

INJECTABLE_TOOL_NAMES: frozenset[str] = frozenset(
    function.name for function in _SANDBOX_FUNCTIONS if function.tool_cls.registered
)
"""Every model tool a program can also be given, whether or not a run is.

The domain of ``settings.tools.sandbox_only``: naming anything else would
withhold a tool from the model and hand it to nobody, or move a function no
tool list carries in the first place, so the boot check refuses
it (:func:`~hivegent.agents.check_tool_settings`).
"""


def _live(deps: UserDeps) -> tuple[_SandboxFunction, ...]:
    """The functions this run has not withheld."""
    return tuple(
        function
        for function in _SANDBOX_FUNCTIONS
        if function.name not in deps.withheld_tools
    )


def sandbox_surface(ctx: RunContext[UserDeps]) -> MontySurface:
    """Build the host functions and stub for the tools this run may call.

    Gated on exactly what gates the tool of the same name: ``web_enabled`` has
    already dropped the web pair from :data:`_SANDBOX_FUNCTIONS`, and
    :attr:`~hivegent.agents.common.UserDeps.withheld_tools` joins the
    operator's ``settings.tools.disabled`` to the request's own.  A tool
    withheld from the model's tool list must not reappear as a function, since
    the two are one namespace and injecting it would be the side door the
    exclusion exists to close.  A function only a program is handed answers to
    the same names.

    The mode gates nothing here, because nothing here writes: a run that may
    not touch the workspace is still free to search it.

    One builder for the prompt and for the call, so the stub the model was
    given and the stub the type checker enforces cannot come apart.
    """
    live = _live(ctx.deps)

    return monty_surface({function.factory: function.build(ctx) for function in live})


def sandbox_instructions(ctx: RunContext[UserDeps]) -> str:
    """Declare the sandbox's tool surface, or say nothing when it has none.

    Composed per run rather than written once, since which functions exist is
    what the gate above decides, and a stub naming a function the program would
    get a ``NameError`` for is worse than no stub at all.  A function's own
    guidance (``Tool.sandbox_instructions``) follows it in and out.

    ``declarations`` and not ``stubs``: the mount's ``open`` belongs to the type
    checker, and showing the model a declaration of a builtin it already knows
    would spend context saying nothing.  The prose is in the run's language,
    while the declarations stay English like every other tool schema.
    """
    live = _live(ctx.deps)
    declarations = monty_declarations([function.factory for function in live])

    if not declarations:
        return ""

    language = ctx.deps.language
    blocks = [sandbox_api_instructions(declarations)[language]]
    blocks.extend(
        guidance[language]
        for function in live
        if (guidance := function.tool_cls.sandbox_instructions) is not None
    )

    if settings.sandbox.type_check:
        blocks.append(SANDBOX_TYPE_CHECK_INSTRUCTIONS[language])

    return "".join(blocks)


# A factory runs per tool call, so it only wires up fields.  The worker pool
# comes from the lifespan.  `paths` mounts the same roots the read tools span,
# filters applied, so a program reaches what the read tools reach and no more,
# while `writable` is the narrower span its changes may touch, only `/tmp` in a
# mode that may not write, and `commit` lands them once it succeeded.
def _run_python(ctx: RunContext[UserDeps]) -> RunPythonTool:
    deps = ctx.deps

    return RunPythonTool(
        pool=get_monty_pool(),
        limits=_limits,
        paths=deps.search_paths(),
        writable=program_paths(deps),
        commit=changeset_committer(deps),
        changeset_limits=_changeset_limits,
        surface=sandbox_surface(ctx),
        max_host_calls=settings.sandbox.max_host_calls,
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


python_toolset: FunctionToolset[UserDeps] = FunctionToolset()


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
    python_toolset,
    UserDeps,
    _run_python,
    takes_ctx=True,
    args_validator=validate_run_python,
)
