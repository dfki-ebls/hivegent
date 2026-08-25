"""Mutation-oriented MCP tool registrations."""

from collections.abc import Awaitable

from fastmcp import Context
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from mcp.types import InputRequiredResult

from ... import workspace
from ...config import settings
from ...store import Casebase, scoped_operation
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import SearchPath, ToolOutput, tool_description, translate_tool_retry
from ...tools.mutations import (
    DocumentContentArg,
    DocumentTargetPathArg,
    EditNewStringArg,
    EditOldStringArg,
    EditReplaceAllArg,
    ExpectedHashArg,
    WriteModeArg,
)
from ...workspace_events import announcing_mutator
from ..app import mcp_app
from ..common import get_mcp_user_store
from ..confirmation import MUTATION_ANNOTATIONS, PendingMutation, confirm_mutation

__all__ = ["edit_document", "write_document"]


async def _apply(result: Awaitable[ToolOutput[str]]) -> str:
    """Await a mutation, surfacing a ToolRetry as a FastMCP ToolError."""
    with translate_tool_retry(ToolError):
        return (await result).data


@mcp_app.tool(
    description=tool_description(EditDocumentTool), annotations=MUTATION_ANNOTATIONS
)
async def edit_document(
    file_path: DocumentTargetPathArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
    ctx: Context,
    replace_all: EditReplaceAllArg = False,
    expected_hash: ExpectedHashArg = None,
    store: Casebase = Depends(get_mcp_user_store),
) -> str | InputRequiredResult:
    ask = confirm_mutation(
        ctx,
        PendingMutation(
            summary=f"edit to '{file_path}'",
            payload=(old_string, new_string, str(replace_all)),
        ),
    )
    if ask is not None:
        return ask

    tool = EditDocumentTool(
        paths=SearchPath(
            path=store.workspace_dir(settings.data_dir), scope=store.scope
        ),
        mutator=announcing_mutator(
            scoped_operation(workspace.edit_document_text, (store,)), store.id
        ),
    )
    return await _apply(
        tool(file_path, old_string, new_string, replace_all, expected_hash)
    )


@mcp_app.tool(
    description=tool_description(WriteDocumentTool), annotations=MUTATION_ANNOTATIONS
)
async def write_document(
    file_path: DocumentTargetPathArg,
    content: DocumentContentArg,
    ctx: Context,
    mode: WriteModeArg = "replace",
    expected_hash: ExpectedHashArg = None,
    store: Casebase = Depends(get_mcp_user_store),
) -> str | InputRequiredResult:
    ask = confirm_mutation(
        ctx,
        PendingMutation(
            summary=f"{mode} of '{file_path}' ({len(content)} characters)",
            payload=(content,),
        ),
    )
    if ask is not None:
        return ask

    tool = WriteDocumentTool(
        paths=SearchPath(
            path=store.workspace_dir(settings.data_dir), scope=store.scope
        ),
        mutator=announcing_mutator(
            scoped_operation(workspace.write_document_text, (store,)), store.id
        ),
    )
    return await _apply(tool(file_path, content, mode, expected_hash))
