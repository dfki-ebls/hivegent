"""Mutation-oriented MCP tool registrations.

Each tool plans its changeset before it asks, so the user is only ever asked
to confirm a change the gateway would accept, and every item of it is in the
one question.
"""

from collections.abc import Awaitable

from fastmcp import Context
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from mcp.types import InputRequiredResult

from ... import workspace
from ...changes import Changeset
from ...config import settings
from ...humanize import pluralize
from ...store import Casebase, build_search_paths
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import SearchPath, ToolOutput, tool_description, translate_tool_retry
from ...tools.mutations import (
    DocumentContentArg,
    DocumentEditsArg,
    DocumentTargetPathArg,
    ExpectedHashArg,
    WriteModeArg,
    edit_changeset,
    mutation_errors,
    write_changeset,
)
from ..app import mcp_app
from ..common import get_mcp_user_store
from ..confirmation import MUTATION_ANNOTATIONS, PendingMutation, confirm_mutation

__all__ = ["edit_document", "write_document"]


def _paths(store: Casebase) -> tuple[SearchPath, ...]:
    """The one root an MCP mutation reaches, with no conversation and so no ``/tmp``."""
    return build_search_paths(store, (), settings.data_dir)[:1]


def _gateway(store: Casebase) -> workspace.Gateway:
    return workspace.Gateway((store,), store.id)


async def _plan(store: Casebase, changeset: Changeset[str]) -> None:
    """Refuse what the gateway would refuse, before the user is asked."""
    with mutation_errors(ToolError):
        _ = await _gateway(store).plan(changeset)


async def _apply(result: Awaitable[ToolOutput[str]]) -> str:
    """Await a mutation, surfacing a ToolRetry as a FastMCP ToolError."""
    with translate_tool_retry(ToolError):
        return (await result).data


@mcp_app.tool(
    description=tool_description(EditDocumentTool), annotations=MUTATION_ANNOTATIONS
)
async def edit_document(
    file_path: DocumentTargetPathArg,
    edits: DocumentEditsArg,
    ctx: Context,
    expected_hash: ExpectedHashArg = None,
    store: Casebase = Depends(get_mcp_user_store),
) -> str | InputRequiredResult:
    with translate_tool_retry(ToolError):
        changeset = edit_changeset(_paths(store), file_path, edits, expected_hash)

    await _plan(store, changeset)
    count = len(edits)
    ask = confirm_mutation(
        ctx,
        PendingMutation(
            summary=f"{count} {pluralize(count, 'edit')} to '{file_path}'",
            payload=tuple(
                part
                for edit in edits
                for part in (edit.old_string, edit.new_string, str(edit.replace_all))
            ),
        ),
    )
    if ask is not None:
        return ask

    tool = EditDocumentTool(paths=_paths(store), commit=_gateway(store).commit)

    return await _apply(tool(file_path, edits, expected_hash))


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
    with translate_tool_retry(ToolError):
        changeset = write_changeset(_paths(store), file_path, content, mode, expected_hash)

    await _plan(store, changeset)
    ask = confirm_mutation(
        ctx,
        PendingMutation(
            summary=f"{mode} of '{file_path}' ({len(content)} characters)",
            payload=(content,),
        ),
    )
    if ask is not None:
        return ask

    tool = WriteDocumentTool(paths=_paths(store), commit=_gateway(store).commit)

    return await _apply(tool(file_path, content, mode, expected_hash))
