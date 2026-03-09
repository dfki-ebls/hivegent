"""Write-oriented agent tool registrations."""

from functools import partial

from pydantic_ai import FunctionToolset

from ...chunks import on_document_write
from ...config import settings
from ...tools import EditDocumentTool, WriteDocumentTool
from ..common import UserDeps
from ...tools.pydantic_ai import register_agent_tools

__all__ = ["write_toolset"]


def _edit_document(deps: UserDeps) -> EditDocumentTool:
    return EditDocumentTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
        on_write=partial(on_document_write, deps.store),
    )


def _write_document(deps: UserDeps) -> WriteDocumentTool:
    return WriteDocumentTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
        on_write=partial(on_document_write, deps.store),
    )


write_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tools(write_toolset, UserDeps, [
    _edit_document,
    _write_document,
], requires_approval=True)
