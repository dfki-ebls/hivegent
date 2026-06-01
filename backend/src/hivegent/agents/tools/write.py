"""Write-oriented agent tool registrations."""

from functools import partial

from pydantic_ai import FunctionToolset

from ... import workspace
from ...config import settings
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import SearchPath
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["write_toolset"]


def _edit_document(deps: UserDeps) -> EditDocumentTool:
    return EditDocumentTool(
        paths=SearchPath(
            path=deps.store.workspace_dir(settings.data_dir),
            filter_func=deps.document_filter,
        ),
        mutator=partial(workspace.edit_document_text, deps.store),
    )


def _write_document(deps: UserDeps) -> WriteDocumentTool:
    return WriteDocumentTool(
        paths=SearchPath(
            path=deps.store.workspace_dir(settings.data_dir),
            filter_func=deps.document_filter,
        ),
        mutator=partial(workspace.write_document_text, deps.store),
    )


write_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)

register_agent_tools(
    write_toolset,
    UserDeps,
    [
        _edit_document,
        _write_document,
    ],
    requires_approval=True,
)
