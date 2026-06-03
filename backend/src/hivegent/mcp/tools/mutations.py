"""Mutation-oriented MCP tool registrations."""

from functools import partial

from fastmcp import Context
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from ... import workspace
from ...config import settings
from ...store import Casebase
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import SearchPath, tool_description
from ...tools.documents import DocumentFilePathArg
from ...tools.mutations import (
    DocumentContentArg,
    EditNewStringArg,
    EditOldStringArg,
    EditReplaceAllArg,
    WriteModeArg,
)
from ..app import mcp_app
from ..common import get_mcp_user_store

__all__ = ["edit_document", "write_document"]


@mcp_app.tool(description=tool_description(EditDocumentTool))
async def edit_document(
    file_path: DocumentFilePathArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
    ctx: Context,
    replace_all: EditReplaceAllArg = False,
    store: Casebase = Depends(get_mcp_user_store),
) -> str:
    response = await ctx.elicit(
        message=(
            f"Allow edit to '{file_path}'?\n\n"
            f"Replace:\n{old_string!r}\n\nWith:\n{new_string!r}"
        ),
        response_type=None,
    )
    if response.action != "accept":
        return "Edit denied by user."

    tool = EditDocumentTool(
        paths=SearchPath(path=store.workspace_dir(settings.data_dir), prefix=store.prefix),
        mutator=partial(workspace.edit_document_text, store),
    )
    result = await tool(file_path, old_string, new_string, replace_all)
    return result.data


@mcp_app.tool(description=tool_description(WriteDocumentTool))
async def write_document(
    file_path: DocumentFilePathArg,
    content: DocumentContentArg,
    ctx: Context,
    mode: WriteModeArg = "replace",
    store: Casebase = Depends(get_mcp_user_store),
) -> str:
    action = "Create/overwrite" if mode == "replace" else mode.capitalize()
    response = await ctx.elicit(
        message=f"Allow {action} '{file_path}' ({len(content)} chars)?",
        response_type=None,
    )
    if response.action != "accept":
        return "Write denied by user."

    tool = WriteDocumentTool(
        paths=SearchPath(path=store.workspace_dir(settings.data_dir), prefix=store.prefix),
        mutator=partial(workspace.write_document_text, store),
    )
    result = await tool(file_path, content, mode)
    return result.data
