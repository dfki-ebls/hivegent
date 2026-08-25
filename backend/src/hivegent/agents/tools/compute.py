"""Compute-oriented agent tool registrations.

The tool class is settings-free, so this module is where the application
settings are applied to its instance fields, and where the sandbox budget is
translated into the ``ResourceLimits`` the worker enforces.
"""

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_monty import ResourceLimits

from ...config import settings
from ...sandbox import get_monty_pool
from ...tools.pydantic_ai import register_agent_tool
from ...tools.python import PythonOutputPathArg, RunPythonTool
from ..common import UserDeps
from .write import write_document

__all__ = ["compute_toolset"]

_limits: ResourceLimits = {
    "max_duration_secs": settings.sandbox.max_duration_seconds,
    "max_memory": settings.sandbox.max_memory_bytes,
}


# A factory runs per tool call, so it only wires up fields.  The worker pool
# comes from the lifespan.
def _run_python(deps: UserDeps) -> RunPythonTool:
    return RunPythonTool(
        pool=get_monty_pool(),
        limits=_limits,
        paths=deps.python_paths(),
        writer=write_document(deps, deps.python_paths(writable=True))
        if deps.can_write
        else None,
    )


# The validator is called with the whole argument mapping, so naming only the
# one argument it decides on keeps it from restating a signature the adapter
# layer otherwise derives from `RunPythonTool.__call__`.
def _validate_run_python(
    ctx: RunContext[UserDeps],
    output_path: PythonOutputPathArg = None,
    **_rest: object,
) -> None:
    """Require approval only when the otherwise read-only tool will persist output."""
    if output_path is None:
        return

    if not ctx.deps.can_write:
        raise ModelRetry("Workspace writes are unavailable in this chat mode.")

    if ctx.deps.needs_approval and not ctx.tool_call_approved:
        raise ApprovalRequired({"output_path": output_path})


compute_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tool(
    compute_toolset,
    UserDeps,
    _run_python,
    args_validator=_validate_run_python,
)
