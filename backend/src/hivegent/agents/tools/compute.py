"""Compute-oriented agent tool registrations.

The tool class is settings-free, so this module is where the application
settings are applied to its instance fields, and where the sandbox budget is
translated into the ``ResourceLimits`` the worker enforces.
"""

from pydantic_ai import FunctionToolset
from pydantic_monty import ResourceLimits

from ...config import settings
from ...sandbox import get_monty_pool
from ...tools.pydantic_ai import register_agent_tool
from ...tools.python import RunPythonTool
from ..common import UserDeps
from .write import output_sink, validate_output_write

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
        paths=deps.working_paths(),
        writer=output_sink(deps),
    )


compute_toolset: FunctionToolset[UserDeps] = FunctionToolset()

# The sandbox names its output itself, so it takes only the gate the redirect
# argument elsewhere shares with it — both persist on the model's say-so.
register_agent_tool(
    compute_toolset,
    UserDeps,
    _run_python,
    args_validator=validate_output_write,
)
