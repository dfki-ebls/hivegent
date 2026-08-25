"""Compute-oriented agent tool registrations.

The tool class is settings-free, so this module is where the application
settings are applied to its instance fields, and where the sandbox budget is
translated into the ``ResourceLimits`` the worker enforces.
"""

from pydantic_ai import FunctionToolset
from pydantic_monty import ResourceLimits

from ...config import settings
from ...sandbox import get_monty_pool
from ...tools import RunPythonTool
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["compute_toolset"]

_limits: ResourceLimits = {
    "max_duration_secs": settings.sandbox.max_duration_seconds,
    "max_memory": settings.sandbox.max_memory_bytes,
}


# A factory runs per tool call, so it only wires up fields.  The worker pool
# comes from the lifespan.
def _run_python(_deps: UserDeps) -> RunPythonTool:
    return RunPythonTool(pool=get_monty_pool(), limits=_limits)


compute_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tools(compute_toolset, UserDeps, [_run_python])
