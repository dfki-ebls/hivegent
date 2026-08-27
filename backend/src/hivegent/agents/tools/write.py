"""Write-oriented agent tool registrations.

Also the one place the two workspace writes an agent performs on its own
account are wired: the ``output_path`` a tool redirects into and the one the
sandbox declares. Both commit through the same gateway the write tools use and
answer to the same gate, so a run can never persist by a side door what the
mode forbids it to write outright.

That gate is applied per call rather than declared per tool, because whether a
write needs a human is a property of the path it names: a document is the
user's and asks, a `.scratch/` file is the run's own working state and does
not.  The mode still decides the rest — ``read`` and ``plan`` refuse every
write, ``write`` approves every one of them.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Concatenate

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry

from ... import workspace
from ...entries import is_scratch_path
from ...store import Casebase, scoped_operation
from ...tools import EditDocumentTool, WriteDocumentTool
from ...tools.base import resolve_accessible_file, translate_tool_retry
from ...tools.mutations import MutationHint
from ...tools.pydantic_ai import register_agent_tools
from ...tools.python import is_python_script
from ...tools.sink import OutputPathArg, output_format
from ...workspace_events import announcing_mutator
from ..common import UserDeps

__all__ = [
    "output_sink",
    "validate_document_write",
    "validate_output_path",
    "validate_output_write",
    "write_document",
    "write_toolset",
]


def _mutator[**P, R](
    deps: UserDeps,
    operation: Callable[Concatenate[Casebase, str, P], Awaitable[R]],
) -> Callable[Concatenate[str, P], Awaitable[R]]:
    """Front *operation* with the canonical path and the notification it owes.

    The one place the writable-store span is bound, so every tool built here
    reaches exactly the workspaces the gate below was applied against.
    """
    return announcing_mutator(
        scoped_operation(operation, deps.writable_stores), deps.user_id
    )


def _run_python_pointer(target: str, local: str) -> str:
    """Point a stored program at the tool that runs it.

    A `.scratch/` `.py` is written in order to be run, and the mutation that
    stored or repaired it is the one moment its canonical path is in hand, so
    the pointer rides the receipt rather than waiting for the instructions to
    be recalled a turn later: what it saves is the run that pastes the program
    straight back in as inline ``code``.  Injected on this surface alone,
    since the MCP one writes through the same tools and has no ``run_python``.
    """
    if not is_scratch_path(local) or not is_python_script(local):
        return ""

    return f"Run it with run_python's `script_path='{target}'`."


def _edit_document(deps: UserDeps) -> EditDocumentTool:
    return EditDocumentTool(
        paths=deps.search_paths(writable=True),
        hint=_run_python_pointer,
        mutator=_mutator(deps, workspace.edit_document_text),
    )


def write_document(
    deps: UserDeps, hint: MutationHint | None = None
) -> WriteDocumentTool:
    """Build the canonical scoped document writer for one agent run.

    Public because ``run_python`` composes it through :func:`output_sink` to
    commit its declared output, so the output it writes is scoped exactly like
    the files it read.  That path passes no *hint*: a commit the model asked
    for by declaring an ``output_path`` is not a program it just stored.
    """
    return WriteDocumentTool(
        paths=deps.search_paths(writable=True),
        hint=hint,
        mutator=_mutator(deps, workspace.write_document_text),
    )


def _write_document(deps: UserDeps) -> WriteDocumentTool:
    """The agent's own writer, which points a stored program at ``run_python``."""
    return write_document(deps, _run_python_pointer)


def output_sink(deps: UserDeps) -> WriteDocumentTool | None:
    """Build the writer a tool's ``output_path`` redirect commits through.

    ``None`` in a mode that may not write, so the redirect is refused in words
    the model can act on rather than silently dropped.
    """
    if not deps.can_write:
        return None

    return write_document(deps)


def _is_scratch_target(deps: UserDeps, file_path: str) -> bool:
    """Whether *file_path* names run state rather than one of the user's documents.

    Decided on the canonical path the write would land on, not the spelling it
    was addressed by, so neither a ``..`` segment nor a symlink can carry a
    ``.scratch`` part onto an ordinary document and skip the approval with it.
    An unresolvable path is not scratch: the tool refuses it downstream in its
    own words, and until then it is treated like any other document.
    """
    resolved = resolve_accessible_file(deps.search_paths(writable=True), file_path)

    return resolved is not None and is_scratch_path(resolved[1])


def _gate_write(ctx: RunContext[UserDeps], argument: str, path: str) -> None:
    """Apply the mode and approval gate to one workspace write.

    *argument* names the parameter carrying *path*, which is what the approval
    request shows the user.
    """
    if not ctx.deps.can_write:
        raise ModelRetry("Workspace writes are unavailable in this chat mode.")

    if (
        ctx.deps.needs_approval
        and not ctx.tool_call_approved
        and not _is_scratch_target(ctx.deps, path)
    ):
        raise ApprovalRequired({argument: path})


# The validators are called with the whole argument mapping, so naming only the
# one argument each decides on keeps them from restating signatures the adapter
# layer otherwise derives from each tool's `__call__`.
def validate_document_write(
    ctx: RunContext[UserDeps],
    file_path: str,
    **_rest: Any,
) -> None:
    """Apply the mode and approval gate to a document mutation."""
    _gate_write(ctx, "file_path", file_path)


def validate_output_write(
    ctx: RunContext[UserDeps],
    output_path: str | None = None,
    **_rest: Any,
) -> None:
    """Apply the mode and approval gate to an optional workspace output."""
    if output_path is None:
        return

    _gate_write(ctx, "output_path", output_path)


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
        _write_document,
    ],
    args_validator=validate_document_write,
)
