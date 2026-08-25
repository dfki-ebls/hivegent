"""Write-oriented agent tool registrations.

Also the one place the two workspace writes an agent performs on its own
account are wired: the ``output_path`` a tool redirects into and the one the
sandbox declares. Both commit through the same gateway the write tools use and
answer to the same gate, so a run can never persist by a side door what the
mode forbids it to write outright.
"""

from typing import Any

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry

from ... import workspace
from ...store import scoped_operation
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import SearchPath, translate_tool_retry
from ...tools.pydantic_ai import register_agent_tools
from ...tools.sink import OutputPathArg, output_format
from ...workspace_events import announcing_mutator
from ..common import UserDeps

__all__ = [
    "output_sink",
    "validate_output_path",
    "validate_output_write",
    "write_document",
    "write_toolset",
]


def _edit_document(deps: UserDeps) -> EditDocumentTool:
    return EditDocumentTool(
        paths=deps.search_paths(writable=True),
        mutator=announcing_mutator(
            scoped_operation(workspace.edit_document_text, deps.writable_stores),
            deps.user_id,
        ),
    )


def write_document(
    deps: UserDeps, paths: tuple[SearchPath, ...] | None = None
) -> WriteDocumentTool:
    """Build the canonical scoped document writer for one agent run.

    Public because ``run_python`` composes it to commit its declared output,
    passing its own writable roots so the output it writes is scoped exactly
    like the files it read.  Registration ignores the extra parameter: the
    adapter reads the return annotation and calls the factory with the deps
    alone.
    """
    return WriteDocumentTool(
        paths=deps.search_paths(writable=True) if paths is None else paths,
        mutator=announcing_mutator(
            scoped_operation(workspace.write_document_text, deps.writable_stores),
            deps.user_id,
        ),
    )


def output_sink(deps: UserDeps) -> WriteDocumentTool | None:
    """Build the writer a tool's ``output_path`` redirect commits through.

    ``None`` in a mode that may not write, so the redirect is refused in words
    the model can act on rather than silently dropped. The roots are the
    working ones, which keeps `.scratch/` reachable even while the conversation
    is narrowed to a handful of documents.
    """
    if not deps.can_write:
        return None

    return write_document(deps, deps.working_paths(writable=True))


# The validator is called with the whole argument mapping, so naming only the
# one argument it decides on keeps it from restating signatures the adapter
# layer otherwise derives from each tool's `__call__`.
def validate_output_write(
    ctx: RunContext[UserDeps],
    output_path: str | None = None,
    **_rest: Any,
) -> None:
    """Apply the mode and approval gate to an optional workspace output."""
    if output_path is None:
        return

    if not ctx.deps.can_write:
        raise ModelRetry("Workspace writes are unavailable in this chat mode.")

    if ctx.deps.needs_approval and not ctx.tool_call_approved:
        raise ApprovalRequired({"output_path": output_path})


def validate_output_path(
    ctx: RunContext[UserDeps],
    output_path: OutputPathArg = None,
    **_rest: Any,
) -> None:
    """Validate a bulk-result redirect before the tool performs its work."""
    if output_path is not None:
        with translate_tool_retry(ModelRetry):
            _ = output_format(output_path)

    validate_output_write(ctx, output_path)


write_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)

register_agent_tools(
    write_toolset,
    UserDeps,
    [
        _edit_document,
        write_document,
    ],
    requires_approval=True,
)
