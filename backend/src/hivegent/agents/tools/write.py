"""Write-oriented agent tool registrations.

Also the one place the changes a ``run_python`` program makes are wired,
which ``apply_changes`` applies whole once they are staged.  Every write commits through
:func:`_gateway` and answers to :func:`_gate`, so a run can never persist by a
side door what it may not write outright.

Every write resolves against :meth:`~hivegent.agents.common.UserDeps.writable_roots`
and commits as one changeset through the one gateway, all of it or nothing,
whose roots are the ones each search path carries, the conversation's ``/tmp``
a folder written directly, and whose filters are theirs.  Whether to ask is
decided per item by the policy of the root it routes to
(:func:`~hivegent.agents.policy.root_asks`): a :class:`Direct` folder
(``/tmp``) is the run's own working state and asks nothing in any mode, a
:class:`ModeGated` workspace is put in front of the user first in
``interactive`` and lands at once in ``write``, and a :class:`Reviewed` root
always asks.  A mode that may not write leaves the
workspaces out of the writable roots, so they are refused like any path the run
cannot reach.  Before asking, the validator plans the
very changeset the tool would commit, its ``/tmp`` half included, so the user
is never asked to approve a batch the gateway would refuse, and the approval
shows the planner's summary of the workspace half.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import compress
from typing import Any

from exar.memory.mounts import strip
from exar.tools import Retry
from pydantic_ai import DeferredToolRequests, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import ToolDefinition

from ... import staging, workspace
from ...db.conversations import OwnedConversation
from ...tmp import TMP_PREFIX
from ...tools.base import (
    Direct,
    SearchPath,
    ToolFunc,
)
from ...tools.changes import Changeset, TextEdit
from ...tools.changeset import (
    AppliedChanges,
    ChangesetIdArg,
    ChangesetOutcome,
    CommitChanges,
    PendingChanges,
    apply_changes,
)
from ...tools.mutations import (
    DocumentMove,
    WriteModeArg,
    delete_changeset,
    delete_documents,
    edit_changeset,
    edit_document,
    move_changeset,
    move_documents,
    mutation_errors,
    write_changeset,
    write_document,
)
from ...tools.python import is_python_script
from ...workspace.operations import Root
from ..approval import approved_changes, ask, changes_digest
from ..common import AgentTools, Hooked, UserDeps

__all__ = [
    "ProgramWrites",
    "program_writes",
    "retain_awaited_changes",
    "validate_apply_changes",
    "validate_document_deletions",
    "validate_document_edit",
    "validate_document_moves",
    "validate_document_write",
    "write_tools",
    "write_toolset",
]

APPLY_CHANGES = "apply_changes"
"""The tool that applies a program's staged changes."""

_RERUN = "Run the program again to stage a fresh changeset."


def _search_paths(roots: Sequence[Root]) -> tuple[SearchPath, ...]:
    """What the path tools of *roots* resolve against."""
    return tuple(root.search_path for root in roots)


def _asks(
    deps: UserDeps, gateway: workspace.Gateway, changeset: Changeset[str]
) -> bool:
    """Whether *changeset* waits for the user, by the policy of each root it changes."""
    return any(map(deps.asks, gateway.policies(changeset)))


_CHANGED = (
    "The changes this call would make are no longer the ones the user approved, "
    "so nothing was changed. Call it again to show the user the current changes."
)


async def _hold(
    ctx: RunContext[UserDeps], gateway: workspace.Gateway, changeset: Changeset[str]
) -> None:
    """Ask about *changeset* if any root it changes asks, or hold an approval to the changes it was given for.

    An approval bound to a summary (``approval.APPROVED_CHANGES``) passes only
    while the changeset plans to the same one, an unbound one asks nothing.
    """
    approved, bound = ctx.tool_call_approved, approved_changes(ctx)

    if (approved and bound is None) or (
        not approved and not _asks(ctx.deps, gateway, changeset)
    ):
        return

    summary = (await gateway.plan(changeset)).summary

    if not approved:
        ask(summary)

    if changes_digest(summary) != bound:
        raise ModelRetry(_CHANGED)


async def _gate(
    ctx: RunContext[UserDeps],
    build: Callable[[tuple[SearchPath, ...]], Changeset[str]],
) -> None:
    """Refuse what the tool would refuse, then ask about its gated half if anyone must.

    *build* is the tool's own changeset builder, run on the roots the tool
    resolves against, so the question is asked of exactly what would commit.
    It is asked when any item's root asks, and only then is the changeset
    planned, its ``/tmp`` half included, since the commit refuses with the
    same words.  An approved resume asks nothing (:func:`_hold`).
    """
    roots = ctx.deps.writable_roots()
    paths = _search_paths(roots)
    gateway = workspace.Gateway(roots)

    with mutation_errors(ModelRetry):
        await _hold(ctx, gateway, build(paths))


def _run_python_pointer(target: str) -> str:
    """Point a stored program at the tool that runs it.

    A ``/tmp`` ``.py`` is written in order to be run, and the mutation that
    stored or repaired it is the one moment its canonical path is in hand, so
    the pointer rides the receipt rather than waiting for the instructions to
    be recalled a turn later: what it saves is the run that pastes the program
    straight back in as inline ``code``.
    """
    if strip(TMP_PREFIX, target) is None or not is_python_script(target):
        return ""

    return f"Run it with run_python's `script_path='{target}'`."


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
        ctx,
        lambda paths: write_changeset(paths, file_path, content, mode, expected_hash),
    )


async def validate_document_edit(
    ctx: RunContext[UserDeps],
    file_path: str,
    edits: Sequence[TextEdit],
    expected_hash: str | None = None,
    **_rest: Any,
) -> None:
    """Refuse an edit the tool would refuse, then gate it with every replacement."""
    await _gate(
        ctx, lambda paths: edit_changeset(paths, file_path, edits, expected_hash)
    )


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


def _program_roots(deps: UserDeps) -> tuple[Root, ...]:
    """The roots a ``run_python`` program may change.

    The workspaces only where ``apply_changes`` is not withheld, by the request
    or the operator: that tool is the switch for a program's workspace
    changes, so withholding it must not leave write mode's direct path open as
    a side door.  A run without a conversation has nowhere to stage them.
    """
    staging_allowed = (
        APPLY_CHANGES not in deps.withheld_tools and deps.conversation is not None
    )

    return deps.writable_roots(workspace=staging_allowed)


def _conversation(deps: UserDeps) -> OwnedConversation:
    """The conversation a program's changes are staged in.

    Only a run with one is offered ``apply_changes`` or a writable workspace
    mount, so its absence here is a bug rather than a refusal to explain.
    """
    if deps.conversation is None:
        raise RuntimeError("staging needs the run's conversation")

    return deps.conversation


def _program_commit(deps: UserDeps, gateway: workspace.Gateway) -> CommitChanges:
    """Build what lands a ``run_python`` program's changes through *gateway*.

    Each item asks or not by the policy of the root it routes to.  Where none
    asks every item is applied at once, as :func:`_gate` lets such a write
    through, and the reports of the items beyond ``/tmp`` are returned.
    Otherwise the whole changeset, its ``/tmp`` half included, is planned, so a
    program whose changes the gateway would refuse is told so at once, and
    stored for :func:`validate_apply_changes` to put in front of the user, so
    it lands in full once approved or not at all, as :func:`_hold` keeps a
    single call's.
    """

    async def commit(changeset: Changeset[str]) -> ChangesetOutcome | None:
        with mutation_errors(Retry):
            policies = gateway.policies(changeset)
            persistent = tuple(not isinstance(policy, Direct) for policy in policies)

            if any(map(deps.asks, policies)):
                summary = (await gateway.plan(changeset)).summary
                changeset_id = await staging.stage(_conversation(deps), changeset)

                return PendingChanges(changeset_id, summary, persistent.count(False))

            applied = await gateway.apply(changeset)

        return (
            AppliedChanges(tuple(compress(applied.reports, persistent)))
            if any(persistent)
            else None
        )

    return commit


@dataclass(frozen=True, slots=True)
class ProgramWrites:
    """What a ``run_python`` program may change, and what lands its changes."""

    paths: tuple[SearchPath, ...]
    commit: CommitChanges


def program_writes(deps: UserDeps) -> ProgramWrites:
    """The roots a ``run_python`` program of *deps* may change, and their commit."""
    roots = _program_roots(deps)

    return ProgramWrites(
        _search_paths(roots), _program_commit(deps, workspace.Gateway(roots))
    )


async def _load_staged(deps: UserDeps, changeset_id: str) -> Changeset[str]:
    staged = await staging.load_staged(_conversation(deps), changeset_id)

    if staged is None:
        raise Retry(
            f"No staged changeset '{changeset_id}': it was applied already, "
            f"expired, or never existed. {_RERUN}"
        )

    return staged


async def validate_apply_changes(
    ctx: RunContext[UserDeps], changeset_id: ChangesetIdArg, **_rest: Any
) -> None:
    """Plan a staged changeset again, then put its summary in front of the user.

    It is asked about by the policy of each root it changes, as when it was
    staged.  An approved resume skips both, since applying it loads and refuses
    alike (:func:`_hold`).
    """
    if ctx.tool_call_approved and approved_changes(ctx) is None:
        return

    gateway = workspace.Gateway(ctx.deps.writable_roots())
    staged = await _load_staged(ctx.deps, changeset_id)

    with mutation_errors(ModelRetry, _RERUN):
        await _hold(ctx, gateway, staged)


def _apply_changes(deps: UserDeps, gateway: workspace.Gateway) -> ToolFunc:
    async def apply(changeset_id: str) -> tuple[str, ...]:
        staged = await _load_staged(deps, changeset_id)

        try:
            with mutation_errors(Retry, _RERUN):
                return (await gateway.apply(staged)).reports
        finally:
            await staging.discard_staged(_conversation(deps), changeset_id)

    return apply_changes(apply=apply)


async def retain_awaited_changes(
    conversation: OwnedConversation,
    deferred: DeferredToolRequests | None,
    since: float,
) -> None:
    """Keep only the changesets *conversation* staged since *since* or that a pending ``apply_changes`` call names.

    *since* is the epoch time the saved turn started at and *deferred* are
    the calls its history awaits, so an older changeset none of them names
    was applied, denied, or abandoned, and can never apply.
    """
    approvals = () if deferred is None else deferred.approvals
    await staging.retain_staged(
        conversation,
        {
            changeset_id
            for part in approvals
            if part.tool_name == APPLY_CHANGES
            and isinstance(changeset_id := part.args_as_dict().get("changeset_id"), str)
        },
        since,
    )


def _offer_apply_changes(
    ctx: RunContext[UserDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    # Only a mode that may write the workspaces stages a program's changes
    # there, and only in a conversation, whose changes they are.
    return (
        tool_def if ctx.deps.can_write and ctx.deps.conversation is not None else None
    )


def write_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc | Hooked]:
    """The tools that change files through the gateway, for one run.

    Each one is gated per call by its validator, which plans the changeset the
    tool would commit, and ``apply_changes`` is offered only where a program's
    changes can be staged.
    """
    deps = ctx.deps
    roots = deps.writable_roots()
    paths = _search_paths(roots)
    gateway = workspace.Gateway(roots)
    commit = gateway.commit

    return [
        Hooked(
            edit_document(paths, hint=_run_python_pointer, commit=commit),
            args_validator=validate_document_edit,
        ),
        Hooked(
            write_document(paths, hint=_run_python_pointer, commit=commit),
            args_validator=validate_document_write,
        ),
        Hooked(
            move_documents(paths, commit=commit),
            args_validator=validate_document_moves,
        ),
        Hooked(
            delete_documents(paths, commit=commit),
            args_validator=validate_document_deletions,
        ),
        Hooked(
            _apply_changes(deps, gateway),
            args_validator=validate_apply_changes,
            prepare=_offer_apply_changes,
        ),
    ]


write_toolset = AgentTools(factory=write_tools, gateway=True)
"""The write tools, every write landing through the changeset gateway."""
