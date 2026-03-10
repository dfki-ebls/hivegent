"""Plan creation tool callable."""

from dataclasses import dataclass
from typing import Annotated, override

from pydantic import Field

from .base import Tool

__all__ = [
    "CreatePlanTool",
    "PlanDescriptionArg",
    "PlanStepsArg",
    "PlanTitleArg",
]

PlanTitleArg = Annotated[
    str,
    Field(description="Short title summarizing the plan."),
]
PlanDescriptionArg = Annotated[
    str,
    Field(description="Brief description of what the plan accomplishes."),
]
PlanStepsArg = Annotated[
    list[str],
    Field(description="Ordered list of steps to execute."),
]


@dataclass(slots=True, frozen=True)
class CreatePlanTool(Tool):
    """Present a step-by-step plan for upcoming operations."""

    @override
    async def __call__(
        self,
        title: PlanTitleArg,
        description: PlanDescriptionArg,
        steps: PlanStepsArg,
    ) -> str:
        """Create a plan for multi-step operations.

        Use this tool to present a structured plan to the user before
        executing any changes.  The user will review the plan and decide
        whether to proceed.
        """
        return "Plan created. Awaiting user approval to execute."
