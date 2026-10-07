"""Write-oriented agent tool registrations.

Also the one place the changes a ``run_python`` program makes are wired,
whose workspace half ``apply_changes`` applies.  Every write commits through
:func:`_gateway` and answers to :func:`_gate`, so a run can never persist by a
side door what it may not write outright.

Every write resolves against :meth:`~hivegent.agents.common.UserDeps.writable_paths`
and commits as one changeset through the one gateway, all of it or nothing,
whose roots are those paths, the conversation's ``/tmp`` a folder written
directly, and whose filters are theirs.  Whether to ask is decided per item
from the root it routes to: a folder (``/tmp``) is the run's own working state
and asks nothing in any mode, while a workspace is put in front of the user
first in ``interactive`` and lands at once in ``write``.  A mode that may not
write leaves the workspaces out of the writable roots, so they are refused
like any path the run cannot reach.  Before asking, the validator plans the
very changeset the tool would commit, its ``/tmp`` half included, so the user
is never asked to approve a batch the gateway would refuse, and the approval
shows the planner's summary of the workspace half.
"""

from collections.abc import Callable, Container, Sequence
from typing import Any, NoReturn

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_jsonable_python

from ... import staging, workspace
from ...changes import Changeset, ChangesetSummary, Operation, TextEdit
from ...tmp import TMP_SCOPE, tmp_root
from ...tools import (
    DeleteDocumentsTool,
    EditDocumentTool,
    MoveDocumentsTool,
    WriteDocumentTool,
)
from ...tools.base import (
    PathFilter,
    SearchPath,
    ToolRetry,
    factory_tool_name,
    translate_tool_retry,
)
from ...tools.changeset import (
    AppliedChanges,
    ApplyChangesTool,
    ChangesetIdArg,
    ChangesetOutcome,
    CommitChanges,
    PendingChanges,
)
from ...tools.mutations import (
    Commit,
    DocumentMove,
    WriteModeArg,
    delete_changeset,
    edit_changeset,
    move_changeset,
    mutation_errors,
    write_changeset,
)
from ...tools.pydantic_ai import register_agent_tool
from ...tools.python import is_python_script
from ...workspace.operations import Root
from ..common import UserDeps

__all__ = [
    "changeset_committer",
    "discard_unapproved_changes",
    "program_paths",
    "validate_apply_changes",
    "validate_document_deletions",
    "validate_document_edit",
    "validate_document_moves",
    "validate_document_write",
    "write_toolset",
]

_RERUN = "Run the program again to stage a fresh changeset."


def _gateway(deps: UserDeps, paths: tuple[SearchPath, ...] | None = None) -> workspace.Gateway:
    """The gateway bound to the roots *paths* name with their filters, announcing to the run's user.

    *paths* default to every root the run may write.  A path's root is the
    casebase its scope names, or else the conversation's ``/tmp`` folder.
    """
    stores = {store.scope.prefix: store for store in deps.all_stores}
    roots: list[Root] = []
    filters: dict[str, PathFilter] = {}

    for sp in deps.writable_paths() if paths is None else paths:
        root = stores.get(sp.prefixed("")) or tmp_root(sp.path)
        roots.append(root)

        if sp.filter_func is not None:
            filters[root.store_key] = sp.filter_func

    return workspace.Gateway(tuple(roots), deps.user_id, filters)


def _committer(deps: UserDeps) -> Commit:
    """Commit a changeset, every item at once, the commit every write tool shares."""
    return _gateway(deps).commit


def _ask(summary: ChangesetSummary) -> NoReturn:
    """Ask the user to approve the summary the planner shows."""
    raise ApprovalRequired(to_jsonable_python(summary))


def _asks(ctx: RunContext[UserDeps]) -> bool:
    """Whether a gated change of this call still needs the user's answer."""
    return ctx.deps.needs_approval and not ctx.tool_call_approved


async def _gate(
    ctx: RunContext[UserDeps],
    build: Callable[[tuple[SearchPath, ...]], Changeset[str]],
) -> None:
    """Refuse what the tool would refuse, then ask about its gated half if anyone must.

    *build* is the tool's own changeset builder, run on the roots the tool
    resolves against, so the question is asked of exactly what would commit.
    It is asked when any item routes to a workspace, and only then is the
    changeset planned, its ``/tmp`` half included, since the commit refuses
    with the same words.
    """
    paths = ctx.deps.writable_paths()
    gateway = _gateway(ctx.deps, paths)

    with translate_tool_retry(ModelRetry), mutation_errors(ModelRetry):
        changeset = build(paths)

        if not (_asks(ctx) and any(gateway.gated(changeset))):
            return

        summary = (await gateway.plan(changeset)).summary

    _ask(summary)


def _run_python_pointer(target: str) -> str:
    """Point a stored program at the tool that runs it.

    A ``/tmp`` ``.py`` is written in order to be run, and the mutation that
    stored or repaired it is the one moment its canonical path is in hand, so
    the pointer rides the receipt rather than waiting for the instructions to
    be recalled a turn later: what it saves is the run that pastes the program
    straight back in as inline ``code``.  Injected on this surface alone,
    since the MCP one writes through the same tools and has no ``run_python``.
    """
    if TMP_SCOPE.strip_prefix(target) is None or not is_python_script(target):
        return ""

    return f"Run it with run_python's `script_path='{target}'`."


def _edit_document(deps: UserDeps) -> EditDocumentTool:
    return EditDocumentTool(
        paths=deps.writable_paths(), hint=_run_python_pointer, commit=_committer(deps)
    )


def _write_document(deps: UserDeps) -> WriteDocumentTool:
    """The agent's own writer, which points a stored program at ``run_python``."""
    return WriteDocumentTool(
        paths=deps.writable_paths(), hint=_run_python_pointer, commit=_committer(deps)
    )


def _move_documents(deps: UserDeps) -> MoveDocumentsTool:
    return MoveDocumentsTool(paths=deps.writable_paths(), commit=_committer(deps))


def _delete_documents(deps: UserDeps) -> DeleteDocumentsTool:
    return DeleteDocumentsTool(paths=deps.writable_paths(), commit=_committer(deps))


# The validators are called with the whole argument mapping, so naming only the
# arguments each decides on keeps them from restating signatures the adapter
# layer otherwise derives from each tool's `__call__`.
async def validate_document_write(
    ctx: RunContext[UserDeps],
    file_path: str,
    content: str,
    mode: WriteModeArg = "replace",
    expected_hash: str | None = None,
    **_rest: Any,
) -> None:
    """Refuse a write the tool would refuse, then gate it."""
    await _gate(
        ctx, lambda paths: write_changeset(paths, file_path, content, mode, expected_hash)
    )


async def validate_document_edit(
    ctx: RunContext[UserDeps],
    file_path: str,
    edits: Sequence[TextEdit],
    expected_hash: str | None = None,
    **_rest: Any,
) -> None:
    """Refuse an edit the tool would refuse, then gate it with every replacement."""
    await _gate(ctx, lambda paths: edit_changeset(paths, file_path, edits, expected_hash))


async def validate_document_moves(
    ctx: RunContext[UserDeps], moves: Sequence[DocumentMove], **_rest: Any
) -> None:
    """Gate every move as one batch and a single decision."""
    await _gate(ctx, lambda paths: move_changeset(paths, moves))


async def validate_document_deletions(
    ctx: RunContext[UserDeps], paths: Sequence[str], **_rest: Any
) -> None:
    """Gate every deletion as one batch and a single decision."""
    await _gate(ctx, lambda roots: delete_changeset(roots, paths))


def program_paths(deps: UserDeps) -> tuple[SearchPath, ...]:
    """The roots a ``run_python`` program may change.

    The workspaces only where ``apply_changes`` is not withheld, by the request
    or the operator: that tool is the switch for a program's workspace
    changes, so withholding it must not leave write mode's direct path open as
    a side door.
    """
    staging_allowed = factory_tool_name(_apply_changes) not in deps.withheld_tools

    return deps.writable_paths(workspace=staging_allowed)


def _picked[T](items: Sequence[T], picks: Sequence[bool], pick: bool) -> tuple[T, ...]:
    return tuple(item for item, picked in zip(items, picks, strict=True) if picked == pick)


def changeset_committer(deps: UserDeps) -> CommitChanges:
    """Build what lands a ``run_python`` program's changes.

    Each item is gated or not by the root it routes to.  In a mode that needs
    no approval every item is applied at once, as :func:`_gate` lets every
    other write through, and the workspace items' reports are returned.
    Otherwise the whole changeset is planned, so a program whose changes the
    gateway would refuse is told so at once and leaves ``/tmp`` as it was, and
    then the direct items are written and the gated ones stored for
    :func:`validate_apply_changes` to put in front of the user.
    """
    gateway = _gateway(deps, program_paths(deps))

    async def commit(changeset: Changeset[str]) -> ChangesetOutcome | None:
        operations: Sequence[Operation[str]] = changeset.operations

        with mutation_errors(ToolRetry):
            gated = gateway.gated(changeset)

            if any(gated) and deps.needs_approval:
                summary = (await gateway.plan(changeset)).summary
                _ = await gateway.apply(Changeset(_picked(operations, gated, False)))
                staged = Changeset(_picked(operations, gated, True))

                return PendingChanges(await staging.stage(deps.user_id, staged), summary)

            applied = await gateway.apply(changeset)

        return AppliedChanges(_picked(applied.reports, gated, True)) if any(gated) else None

    return commit


async def _load_staged(deps: UserDeps, changeset_id: str) -> Changeset[str]:
    staged = await staging.load_staged(deps.user_id, changeset_id)

    if staged is None:
        raise ToolRetry(
            f"No staged changeset '{changeset_id}': it was applied already, "
            f"expired, or never existed. {_RERUN}"
        )

    return staged


async def validate_apply_changes(
    ctx: RunContext[UserDeps], changeset_id: ChangesetIdArg, **_rest: Any
) -> None:
    """Plan a staged changeset again, then put its summary in front of the user.

    An approved resume skips both, since applying it loads and refuses alike.
    """
    if not _asks(ctx):
        return

    with translate_tool_retry(ModelRetry):
        staged = await _load_staged(ctx.deps, changeset_id)

    with mutation_errors(ModelRetry, _RERUN):
        summary = (await _gateway(ctx.deps).plan(staged)).summary

    _ask(summary)


def _apply_changes(deps: UserDeps) -> ApplyChangesTool:
    async def apply(changeset_id: str) -> tuple[str, ...]:
        staged = await _load_staged(deps, changeset_id)

        try:
            with mutation_errors(ToolRetry, _RERUN):
                return (await _gateway(deps).apply(staged)).reports
        finally:
            await staging.discard_staged(deps.user_id, changeset_id)

    return ApplyChangesTool(apply=apply)


async def discard_unapproved_changes(
    owner: str, response: ModelResponse, approved: Container[str]
) -> None:
    """Drop the changesets the ``apply_changes`` calls in *response* name, unless approved.

    *response* ends a turn that awaited approval, so a call *approved* does not
    name was denied or abandoned and can never apply its changeset.
    """
    for part in response.parts:
        if (
            isinstance(part, ToolCallPart)
            and part.tool_name == factory_tool_name(_apply_changes)
            and part.tool_call_id not in approved
            and isinstance(changeset_id := part.args_as_dict().get("changeset_id"), str)
        ):
            await staging.discard_staged(owner, changeset_id)


async def _offer_apply_changes(
    ctx: RunContext[UserDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    # Only a mode that may write the workspaces stages a program's changes there.
    return tool_def if ctx.deps.can_write else None


write_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tool(
    write_toolset, UserDeps, _edit_document, args_validator=validate_document_edit
)
register_agent_tool(
    write_toolset, UserDeps, _write_document, args_validator=validate_document_write
)
register_agent_tool(
    write_toolset, UserDeps, _move_documents, args_validator=validate_document_moves
)
register_agent_tool(
    write_toolset,
    UserDeps,
    _delete_documents,
    args_validator=validate_document_deletions,
)
register_agent_tool(
    write_toolset,
    UserDeps,
    _apply_changes,
    args_validator=validate_apply_changes,
    prepare=_offer_apply_changes,
)
