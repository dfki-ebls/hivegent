"""Plan-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ...tools.plan import CreatePlanTool
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["plan_toolset"]


def _create_plan(deps: UserDeps) -> CreatePlanTool:
    return CreatePlanTool()


plan_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)

register_agent_tools(plan_toolset, UserDeps, [_create_plan])
