"""Retaining and maintaining cases, planned as a changeset the host commits.

Retaining and maintaining plan with cbrkit on the loaded cases, and
`cbrkit.io.render` turns the planned changes into the files they write, a
changeset the host commits like any other write, so the user approves exactly
the files that change.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Annotated, Any

from cbrkit.errors import CbrkitError
from cbrkit.io import render
from cbrkit.model import Changes
from cbrkit.system import Outcome, System
from exar.text import plural
from exar.tools import Retry, effect
from exar.tools.batch import tool_return
from pydantic import Field, JsonValue
from pydantic_ai import ToolReturn

from ..base import SearchPath, ToolFunc
from ..changes import ChangeReason, Changeset
from ..changeset import ChangesetOutcome, CommitChanges, changes_report
from .casebase import (
    _LIMITS,
    Bind,
    Bindings,
    CaseBase,
    CaseBaseCache,
    Limits,
    _bindings,
    _open,
    _too_many,
)
from .query import _examples, _system

__all__ = [
    "CasePlanResult",
    "DryRunArg",
    "MaintainPathArg",
    "RetainPathArg",
    "RetainQueryArg",
    "RetainSolutionsArg",
    "maintain_case_base",
    "retain_cases",
]

# Narrower than `Source.writable`, since a changeset writes text the user reviews as a diff.
_WRITABLE_FORMATS = frozenset({"csv", "json", "jsonl"})


type _Run = Callable[[CaseBase, System[Any]], Awaitable[Outcome[Any]]]
"""Plans the changes of a run without making them."""


async def _revise(
    paths: tuple[SearchPath, ...],
    path: str,
    limits: Limits,
    bindings: Bindings,
    cache: CaseBaseCache | None,
    run: _Run,
) -> tuple[CaseBase, Changes[Any, Any], Changeset[str]]:
    """Plan *run* on the case base, with the changeset that would make its changes.

    Raises:
        Retry: When the case base cannot store cases or the run is refused.
    """
    case_base, listing = await asyncio.to_thread(
        _open, paths, path, limits, bindings.embedder, cache
    )
    source = case_base.config.source

    if source.format not in _WRITABLE_FORMATS or source.graphs and not source.folder:
        raise Retry(
            f"'{case_base.path}' cannot store cases. Only CSV, JSON, and JSON Lines "
            "files of records and folders of JSON files can."
        )

    # The planned files are compared with what the stats of the listing name,
    # so a change made since is refused on commit.
    snapshot = await asyncio.to_thread(listing.read)

    try:
        outcome = await run(case_base, _system(case_base, limits, bindings))
        changes = outcome.changes or Changes[Any, Any]()
        files = (
            await asyncio.to_thread(
                render,
                snapshot.name,
                source,
                case_base.cases,
                changes,
                None if source.folder else snapshot.files[snapshot.name].data,
            )
            if changes.items
            else {}
        )
    except (CbrkitError, ValueError, KeyError, TypeError) as exc:
        raise Retry(f"'{case_base.path}' refused the run: {exc}") from exc

    return case_base, changes, Changeset(tuple(snapshot.operations(files)))


def _reasons(changes: Changes[Any, Any]) -> tuple[ChangeReason, ...]:
    """The reason of every planned change, as the approval of its changeset shows them."""
    return tuple(
        ChangeReason(change.op, change.key, change.reason) for change in changes.items
    )


def _render_changes(changes: Changes[Any, Any]) -> list[str]:
    lines: list[str] = []

    for change in changes.items:
        head = change.op if change.key is None else f"{change.op} {change.key}"
        notes = [
            *([change.rule] if change.rule is not None else []),
            *([f"similarity {change.score:.2f}"] if change.score is not None else []),
            *(
                [f"related {', '.join(map(str, change.related))}"]
                if change.related
                else []
            ),
        ]
        lines.append(f"- {head} ({', '.join(notes)}): {change.reason}")

    return lines


@dataclass(slots=True, frozen=True)
class CasePlanResult:
    """The planned changes of a retain or maintenance run and what became of them."""

    path: str
    plan: JsonValue
    changes: ChangesetOutcome | None = None


@dataclass(slots=True, frozen=True)
class _Planner:
    """What a run that plans changes to a case base needs, shared by the retain and maintenance tools.

    Attributes:
        paths: The search paths case bases are resolved against.
        commit: Writes the changeset or stages it for the user's approval.
        limits: What the case base and the run may cost.
        bind: Gets the embedders and language models of the host.
        cache: Where loaded case bases are kept.
    """

    paths: tuple[SearchPath, ...]
    commit: CommitChanges
    limits: Limits
    bind: Bind | None
    cache: CaseBaseCache | None

    async def __call__(
        self, path: str, run: _Run, dry_run: bool
    ) -> ToolReturn[CasePlanResult]:
        """Plan *run* and commit its changes unless it or the limits of the case base make it a dry run."""
        bindings = await _bindings(self.bind)
        case_base, changes, changeset = await _revise(
            self.paths, path, self.limits, bindings, self.cache, run
        )
        count = len(changes.items)
        lines = [
            f"{case_base.path}: {count} planned {plural(count, 'change')}.",
            *_render_changes(changes),
        ]
        planned_only = case_base.config.limits.dry_run

        if dry_run or planned_only or not changeset.operations:
            outcome = None
            lines.append(
                "Nothing was written. Call again with dry_run=false to make these changes."
                if dry_run and count and not planned_only
                else "Nothing was written."
            )
        else:
            outcome = await self.commit(replace(changeset, reasons=_reasons(changes)))
            lines.append(changes_report(outcome))

        return tool_return(
            CasePlanResult(case_base.path, changes.model_dump(mode="json"), outcome),
            "\n".join(lines),
        )


RetainPathArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path of the case base to store the solutions in: a CSV, JSON, "
            "or JSON Lines file, or a folder of JSON files."
        )
    ),
]

RetainQueryArg = Annotated[
    dict[str, JsonValue],
    Field(description="The problem the solutions solve, a partial record or a graph."),
]

RetainSolutionsArg = Annotated[
    list[dict[str, JsonValue]],
    Field(
        description="The accepted solutions to store, each a record or a graph.",
        min_length=1,
        max_length=20,
    ),
]

DryRunArg = Annotated[
    bool,
    Field(
        description=(
            "Only plan the changes and write nothing. Run it first, then call again "
            "with false to make them."
        )
    ),
]


def retain_cases(
    paths: tuple[SearchPath, ...],
    *,
    commit: CommitChanges,
    limits: Limits = _LIMITS,
    bind: Bind | None = None,
    cache: CaseBaseCache | None = None,
) -> ToolFunc:
    """The tool that stores accepted solutions in a case base below *paths* as its configuration says.

    Args:
        paths: The search paths case bases are resolved against.
        commit: Writes the changeset or stages it for the user's approval.
        limits: What the case base and the run may cost.
        bind: Gets the embedders and language models of the host.
        cache: Where loaded case bases are kept.
    """
    plan = _Planner(paths, commit, limits, bind, cache)

    @effect("write")
    async def retain_cases(
        path: RetainPathArg,
        query: RetainQueryArg,
        solutions: RetainSolutionsArg,
        dry_run: DryRunArg = True,
    ) -> ToolReturn[CasePlanResult]:
        """Store accepted solutions of a problem as new cases of a case base.

        The retain section of the case base's similarity configuration decides
        how, by default every solution becomes a new case.  Every change comes
        with its reason.  Plan with the default dry run first and show the
        plan, then call again with dry_run=false, which writes the changed files
        once the user approved them.
        """

        async def run(case_base: CaseBase, system: System[Any]) -> Outcome[Any]:
            return await system.retain(
                _examples(case_base, [query], "query")[0],
                _examples(case_base, solutions, "solution"),
                dry_run=True,
            )

        return await plan(path, run, dry_run)

    return retain_cases


MaintainPathArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path of the case base to maintain: a CSV, JSON, or JSON Lines "
            "file, or a folder of JSON files."
        )
    ),
]


def maintain_case_base(
    paths: tuple[SearchPath, ...],
    *,
    commit: CommitChanges,
    limits: Limits = _LIMITS,
    bind: Bind | None = None,
    cache: CaseBaseCache | None = None,
) -> ToolFunc:
    """The tool that reviews the cases of a case base below *paths* and removes or merges those it does not need.

    Args:
        paths: The search paths case bases are resolved against.
        commit: Writes the changeset or stages it for the user's approval.
        limits: What the case base and the run may cost.
        bind: Gets the embedders and language models of the host.
        cache: Where loaded case bases are kept.
    """
    plan = _Planner(paths, commit, limits, bind, cache)
    limit = limits.max_maintained

    @effect("write")
    async def maintain_case_base(
        path: MaintainPathArg, dry_run: DryRunArg = True
    ) -> ToolReturn[CasePlanResult]:
        """Review a case base and merge duplicates or remove cases it does not need.

        The maintain section of the case base's similarity configuration decides
        what is reviewed: duplicates, the competence of every case, and the
        consistency of their solutions.  Without one, nothing changes.  Every
        change comes with its reason.
        Plan with the default dry run first and show the plan, then call again
        with dry_run=false, which writes the changed files once the user
        approved them.
        """

        async def run(case_base: CaseBase, system: System[Any]) -> Outcome[Any]:
            if len(case_base.cases) > limit:
                raise Retry(_too_many(limit))

            return await system.maintain(dry_run=True)

        return await plan(path, run, dry_run)

    return maintain_case_base
