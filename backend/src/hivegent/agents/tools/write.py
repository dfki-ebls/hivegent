"""Write-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ... import workspace
from ...store import scoped_operation
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.pydantic_ai import register_agent_tools
from ...workspace_events import announcing_mutator
from ..common import UserDeps

__all__ = ["write_document", "write_toolset"]


def _edit_document(deps: UserDeps) -> EditDocumentTool:
    return EditDocumentTool(
        paths=deps.search_paths(writable=True),
        mutator=announcing_mutator(
            scoped_operation(workspace.edit_document_text, deps.writable_stores),
            deps.user_id,
        ),
    )


def write_document(deps: UserDeps) -> WriteDocumentTool:
    """Build the canonical scoped document writer for one agent run.

    Public because ``run_python`` composes it to commit its declared output;
    :func:`~hivegent.tools.base.factory_tool_name` strips no leading underscore
    it does not have, so the registered tool name is unchanged.
    """
    return WriteDocumentTool(
        paths=deps.search_paths(writable=True),
        mutator=announcing_mutator(
            scoped_operation(workspace.write_document_text, deps.writable_stores),
            deps.user_id,
        ),
    )


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
