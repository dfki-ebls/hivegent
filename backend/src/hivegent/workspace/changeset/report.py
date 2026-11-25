"""What a resolved changeset reports to a person approving it and to the clients following it."""

from collections.abc import Callable, Sequence

from ...tools.changes import (
    MAX_DIFF_CHARS,
    MAX_SUMMARY_DIFF_CHARS,
    ChangesetSummary,
    FileDiff,
    PathMove,
    WorkspaceChanged,
    capped_diff,
)
from ..operations import Location, Root
from ..paths import _shown
from .model import _MakeDir, _Resolved, _TextEffect, _Unit, _Vacate, _vacate_of
from .plan import _Plan


def _shown_files(unit: _Unit) -> tuple[str, ...]:
    """The paths of *unit* a person sees go: a tree's root, an entry's files on disk.

    An entry's description and original, or its description alone when only
    its row is left of it.
    """
    if (entry := unit.entry) is None:
        return (unit.path,)

    root = unit.root.path
    present = (entry.description_path, entry.original_path)

    return tuple(
        path for path in present if path is not None and (root / path).exists()
    ) or (entry.description_path,)


def _workspace_changed(
    resolved: Sequence[_Resolved],
    plan: _Plan,
    scopes: tuple[str, ...],
    include: Callable[[_Resolved], bool],
) -> WorkspaceChanged:
    """How *resolved* changes *scopes*, what the items *include* takes move and delete.

    Blocking, and named before anything changed.  What a client follows takes
    the items of a workspace, and what an approval shows those of every root
    a person may be asked about.
    """
    moves: list[PathMove] = []
    deletes: list[str] = []

    def replaces(root: Root, path: str) -> bool:
        """Whether a file there now goes, deleted by another item."""
        hit = plan.holder(plan.vacated, root, path)
        vacate = None if hit is None else _vacate_of(resolved[hit[0]].effect)

        return (
            vacate is not None
            and vacate.destination is None
            and Location(root, path).full_path.exists()
        )

    for item in resolved:
        match _vacate_of(item.effect) if include(item) else None:
            case None:
                pass
            case _Vacate(unit=unit, destination=None):
                deletes.extend(_shown(unit.root, path) for path in _shown_files(unit))
            case _Vacate(unit=unit, destination=_Unit() as destination):
                moved = dict(zip(unit.files, destination.files, strict=True))
                moves.extend(
                    PathMove(
                        _shown(unit.root, path),
                        _shown(destination.root, moved[path]),
                        is_dir=unit.entry is None,
                        replaces=replaces(destination.root, moved[path]),
                    )
                    for path in _shown_files(unit)
                )

    return WorkspaceChanged(scopes, tuple(moves), tuple(deletes))


def _summarize(resolved: Sequence[_Resolved], plan: _Plan) -> ChangesetSummary:
    """What *resolved* does as a person approving it reads it, before anything changed.

    A direct folder's change is never put to a person, so it is left out.
    """
    changed = _workspace_changed(resolved, plan, (), lambda item: item.shown)
    creates: list[FileDiff] = []
    updates: list[FileDiff] = []
    mkdirs: list[str] = []
    budget = MAX_SUMMARY_DIFF_CHARS

    for item in resolved:
        match item.effect:
            case _ if not item.shown:
                pass
            case _TextEffect(target=target, current=current, content=content):
                shown = target.canonical
                diff = capped_diff(shown, current, content, min(budget, MAX_DIFF_CHARS))
                budget -= len(diff)
                (creates if current is None else updates).append(FileDiff(shown, diff))
            case _MakeDir(target=target):
                mkdirs.append(target.canonical)
            case _Vacate():
                pass

    return ChangesetSummary(
        tuple(creates), tuple(updates), changed.moves, changed.deletes, tuple(mkdirs)
    )
