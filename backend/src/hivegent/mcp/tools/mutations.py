"""Mutation-oriented MCP tool registrations."""

from collections.abc import Awaitable
from functools import partial

from fastmcp import Context
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]
from fastmcp.exceptions import ToolError

from ... import workspace
from ...config import settings
from ...store import Casebase
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import SearchPath, ToolOutput, tool_description, translate_tool_retry
from ...tools.documents import DocumentFilePathArg
from ...tools.mutations import (
    DocumentContentArg,
    EditNewStringArg,
    EditOldStringArg,
    EditReplaceAllArg,
    ExpectedHashArg,
    WriteModeArg,
)
from ..app import mcp_app
from ..common import get_mcp_user_store

__all__ = ["edit_document", "write_document"]


async def _apply(result: Awaitable[ToolOutput[str]]) -> str:
    """Await a mutation, surfacing a ToolRetry as a FastMCP ToolError."""
    with translate_tool_retry(ToolError):
        return (await result).data


@mcp_app.tool(description=tool_description(EditDocumentTool))
async def edit_document(
    file_path: DocumentFilePathArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
    ctx: Context,
    replace_all: EditReplaceAllArg = False,
    expected_hash: ExpectedHashArg = None,
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
        paths=SearchPath(
            path=store.workspace_dir(settings.data_dir), scope=store.scope
        ),
        mutator=partial(workspace.edit_document_text, store),
    )
    return await _apply(
        tool(file_path, old_string, new_string, replace_all, expected_hash)
    )


@mcp_app.tool(description=tool_description(WriteDocumentTool))
async def write_document(
    file_path: DocumentFilePathArg,
    content: DocumentContentArg,
    ctx: Context,
    mode: WriteModeArg = "replace",
    expected_hash: ExpectedHashArg = None,
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
        paths=SearchPath(
            path=store.workspace_dir(settings.data_dir), scope=store.scope
        ),
        mutator=partial(workspace.write_document_text, store),
    )
    return await _apply(tool(file_path, content, mode, expected_hash))
