"""Agent tools that store cases into a case base and maintain it.

Both plan with cbrkit and land what changed like a ``run_python`` program's
changes (:func:`~.write.program_writes`): at once where no root asks,
else staged for ``apply_changes``, whose approval shows every changed file.
A dry run, their default, only plans.
"""

from functools import partial

from pydantic_ai import RunContext

from ...cases import CASE_BASES, bindings
from ...config import settings
from ...tools.base import ToolFunc
from ...tools.cases import Limits, maintain_case_base, retain_cases
from ..common import AgentTools, UserDeps
from .write import program_writes

__all__ = ["cases_tools", "cases_toolset"]


def cases_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """The tools that change case bases, for one run."""
    writes = program_writes(ctx.deps)
    paths, commit = writes.paths, writes.commit
    limits = Limits(max_bytes=settings.limits.max_file_size_bytes)

    return [
        retain_cases(
            paths,
            commit=commit,
            limits=limits,
            bind=partial(bindings, ctx.deps.llm),
            cache=CASE_BASES,
        ),
        maintain_case_base(
            paths,
            commit=commit,
            limits=limits,
            bind=partial(bindings, ctx.deps.llm),
            cache=CASE_BASES,
        ),
    ]


cases_toolset = AgentTools(factory=cases_tools, gateway=True)
"""The case base tools, every write landing through the changeset gateway."""
