"""Mutation-oriented MCP tool registrations."""

from functools import partial

from fastmcp import Context
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from ...chunks import on_document_write
from ...config import settings
from ...store import Casebase
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import tool_description
from ...tools.documents import DocumentFilenameArg
from ...tools.mutations import (
    DocumentContentArg,
    EditNewStringArg,
    EditOldStringArg,
    WriteModeArg,
)
from ..app import mcp_app
from ..common import get_mcp_user_store

__all__ = ["edit_document", "write_document"]


@mcp_app.tool(description=tool_description(EditDocumentTool))
async def edit_document(
    filename: DocumentFilenameArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
    ctx: Context,
    store: Casebase = Depends(get_mcp_user_store),
) -> str:
    response = await ctx.elicit(
        message=(
            f"Allow edit to '{filename}'?\n\n"
            f"Replace:\n{old_string!r}\n\nWith:\n{new_string!r}"
        ),
        response_type=None,
    )
    if response.action != "accept":
        return "Edit denied by user."

    tool = EditDocumentTool(
        paths=store.workspace_dir(settings.data_dir),
        hook=partial(on_document_write, store),
    )
    return await tool(filename, old_string, new_string)


@mcp_app.tool(description=tool_description(WriteDocumentTool))
async def write_document(
    filename: DocumentFilenameArg,
    content: DocumentContentArg,
    ctx: Context,
    mode: WriteModeArg = "replace",
    store: Casebase = Depends(get_mcp_user_store),
) -> str:
    action = "Create/overwrite" if mode == "replace" else mode.capitalize()
    response = await ctx.elicit(
        message=f"Allow {action} '{filename}' ({len(content)} chars)?",
        response_type=None,
    )
    if response.action != "accept":
        return "Write denied by user."

    tool = WriteDocumentTool(
        paths=store.workspace_dir(settings.data_dir),
        hook=partial(on_document_write, store),
    )
    return await tool(filename, content, mode)
