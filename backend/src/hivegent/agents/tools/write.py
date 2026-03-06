"""Write-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ... import tool_runtime
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import tool_description
from ...tools.documents import DocumentFilenameArg
from ...tools.mutations import (
    DocumentContentArg,
    EditNewStringArg,
    EditOldStringArg,
    WriteModeArg,
)
from ..common import UserDeps

__all__ = ["edit_document", "write_document", "write_toolset"]

write_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@write_toolset.tool(
    requires_approval=True,
    description=tool_description(EditDocumentTool),
)
async def edit_document(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
) -> str:
    return await tool_runtime.edit_document(
        ctx.deps.store,
        filename,
        old_string,
        new_string,
        document_filter=ctx.deps.document_filter,
    )


@write_toolset.tool(
    requires_approval=True,
    description=tool_description(WriteDocumentTool),
)
async def write_document(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    content: DocumentContentArg,
    mode: WriteModeArg = "replace",
) -> str:
    return await tool_runtime.write_document(
        ctx.deps.store,
        filename,
        content,
        mode=mode,
        document_filter=ctx.deps.document_filter,
    )
