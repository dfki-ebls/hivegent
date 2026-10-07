"""Unit tests for the roots the agent's mutating tools reach and how each commits.

The write tools search the personal workspace, the groups the user may write
to, and ``/tmp``.  Each accepted path is routed back to the root that claimed
it: a workspace's through the changeset gateway and its approval, ``/tmp``'s
straight into the conversation's folder.
"""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import hivegent.agents.tools.python as python_tools
from hivegent import staging
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.write import (
    _apply_changes,
    _delete_documents,
    _edit_document,
    _move_documents,
    _write_document,
    changeset_committer,
    discard_unapproved_changes,
    validate_apply_changes,
    validate_document_deletions,
    validate_document_edit,
    validate_document_moves,
    validate_document_write,
    write_toolset,
)
from hivegent.changes import (
    Changeset,
    ChangesetSummary,
    FileDiff,
    Move,
    PathChanges,
    PathMove,
    TextEdit,
    Write,
)
from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.store import Casebase
from hivegent.tmp import tmp_dir
from hivegent.tools.base import Direct, PathFilter, ToolRetry
from hivegent.tools.changeset import AppliedChanges, PendingChanges
from hivegent.tools.mutations import DocumentMove
from hivegent.types import DocumentFilter
from hivegent.workspace import Location, PlannedChangeset
from hivegent.workspace import changeset as gateway
from tests.helpers import run_context


@pytest.fixture()
def deps(data_dir: Path) -> UserDeps:
    """Deps with one writable and one read-only group."""
    _ = data_dir
    return UserDeps(
        user_id="u",
        store=Casebase.for_user("u"),
        mode="interactive",
        conversation_id="c1",
        group_stores=(Casebase.for_group("team"), Casebase.for_group("archive")),
        write_group_stores=(Casebase.for_group("team"),),
    )


@pytest.fixture()
def routed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record the ``(store_key, local_path)`` each workspace changeset is routed to.

    A changeset reaching ``/tmp`` is applied for real.
    """
    calls: list[tuple[str, str]] = []
    apply = gateway.apply_changeset

    async def _apply(
        changeset: Changeset[Location],
        *,
        owner: str | None = None,
        filters: Mapping[str, PathFilter] = MappingProxyType({}),
        host: bool = False,
    ) -> gateway.AppliedChangeset:
        if any(loc.root.store is None for loc in changeset.locations):
            return await apply(changeset, owner=owner, filters=filters, host=host)

        calls.extend((loc.root.store_key, loc.path) for loc in changeset.locations)

        return gateway.AppliedChangeset(
            tuple("written" for _ in changeset.operations), PathChanges()
        )

    monkeypatch.setattr(gateway, "apply_changeset", _apply)

    return calls


async def test_writes_to_the_addressed_workspace(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    tool = _write_document(deps)

    assert (await tool("@team/notes/a.md", "hi")).data == "written"
    assert (await tool("~/notes/a.md", "hi")).data == "written"
    assert routed == [("group:team", "notes/a.md"), ("user:u", "notes/a.md")]


async def test_tmp_is_written_directly_and_a_program_there_is_pointed_at_run_python(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    """A `/tmp` `.py` is written to be run, so the receipt says how, edit included."""
    folder = tmp_dir(settings.data_dir, "c1")
    pointer = "Run it with run_python's `script_path='/tmp/report.py'`."

    written = (await _write_document(deps)("/tmp/report.py", "print(1)")).data
    edited = (await _edit_document(deps)("/tmp/report.py", [TextEdit("1", "2")])).data

    assert written == f"Wrote 8 characters to '/tmp/report.py'. {pointer}"
    assert edited == f"Replaced 1 occurrence in '/tmp/report.py'. {pointer}"
    assert (folder / "report.py").read_text() == "print(2)"

    with pytest.raises(ToolRetry, match="changed since it was read"):
        await _edit_document(deps)("/tmp/report.py", [TextEdit("2", "3")], "stale")

    # Anything else carries no pointer back to run_python.
    assert (await _write_document(deps)("/tmp/rows.json", "[]")).data == (
        "Wrote 2 characters to '/tmp/rows.json'."
    )

    # Moves and deletes reach `/tmp` too, and land at once.
    _ = await _move_documents(deps)([DocumentMove("/tmp/rows.json", "/tmp/old/rows.json")])
    assert (folder / "old" / "rows.json").read_text() == "[]"
    _ = await _delete_documents(deps)(["/tmp/old/rows.json"])
    assert not (folder / "old" / "rows.json").exists()

    with pytest.raises(ToolRetry, match="only one of them is written directly"):
        await _move_documents(deps)([DocumentMove("/tmp/report.py", "~/report.py")])

    assert routed == []


async def test_tmp_refuses_what_would_outgrow_it(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap counts what a write replaces, so a full folder may still shrink."""
    monkeypatch.setattr(settings.tmp, "max_bytes", 4)
    tool = _write_document(deps)
    _ = await tool("/tmp/a.txt", "1234")

    with pytest.raises(ToolRetry, match="over the"):
        await tool("/tmp/b.txt", "5")

    _ = await tool("/tmp/a.txt", "12")
    _ = await tool("/tmp/b.txt", "34")


async def test_unprefixed_path_is_refused_with_the_roots_named(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    """Nothing is implied by context, so the refusal says what to write instead."""
    tool = _write_document(deps)

    with pytest.raises(ToolRetry, match=r"addresses ~, @team, /tmp; give the full path"):
        await tool("notes/a.md", "hi")

    assert routed == []


async def test_refuses_a_group_the_user_may_only_read(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    tool = _write_document(deps)

    with pytest.raises(ToolRetry, match="not accessible"):
        await tool("@archive/notes/a.md", "hi")

    assert routed == []


def test_run_python_paths_are_lazy_and_end_with_the_conversation_s_tmp(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    filtered = replace(
        deps,
        document_filter=DocumentFilter(excluded=frozenset({"other.md"})),
        group_filters={
            "team": DocumentFilter(excluded=frozenset({"other.md"})),
            "archive": DocumentFilter(excluded=frozenset({"other.md"})),
        },
    )
    pool = object()
    monkeypatch.setattr(python_tools, "get_monty_pool", lambda: pool)

    tool = python_tools._run_python(run_context(filtered))

    assert tool.pool is pool
    assert all(not path.path.exists() for path in tool.resolved_paths)
    *workspaces, tmp = tool.resolved_paths
    assert len(workspaces) == 3

    for path in workspaces:
        assert path.filter_func is not None
        assert path.filter_func("report.md")
        assert not path.filter_func("other.md")

    assert tmp.prefixed("") == "/tmp"
    assert tmp.policy == Direct(reserved=".tool-results")
    assert tool.environ["TMPDIR"] == "/tmp"
    assert len(tool.writable) == 3

    # Read mode, or a withheld apply_changes, changes no workspace and keeps `/tmp`.
    for narrowed in (
        replace(filtered, mode="read"),
        replace(filtered, disabled_tools=frozenset({"apply_changes"})),
    ):
        assert python_tools._run_python(run_context(narrowed)).writable == (tmp,)


def _asked(exc: pytest.ExceptionInfo[ApprovalRequired]) -> dict[str, Any]:
    """The summary an approval request shows."""
    metadata = exc.value.metadata
    assert isinstance(metadata, dict)

    return metadata


def _context(
    deps: UserDeps, mode: str, *, approved: bool = False
) -> RunContext[UserDeps]:
    """A run context in *mode*, which is all any validator here reads."""
    return RunContext(
        deps=replace(deps, mode=mode),
        model=TestModel(),
        usage=RunUsage(),
        tool_call_approved=approved,
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "program is empty"),
        ({"code": None, "script_path": None}, "program is empty"),
        ({"code": "  ", "script_path": "\n"}, "program is empty"),
        ({"code": "1", "script_path": "/tmp/run.py"}, "both given"),
    ],
)
def test_invalid_program_is_refused_before_it_runs(
    deps: UserDeps, arguments: dict[str, str | None], message: str
) -> None:
    tool = python_tools.python_toolset.tools["run_python"]
    validated = tool.function_schema.validator.validate_python(arguments)
    assert tool.args_validator is python_tools.validate_run_python

    with pytest.raises(ModelRetry, match=message):
        python_tools.validate_run_python(_context(deps, "interactive"), **validated)


@pytest.mark.parametrize(
    ("code", "script_path"),
    [
        ("1 + 1", None),
        (None, "/tmp/run.py"),
        ("", "/tmp/run.py"),
        ("1 + 1", " "),
    ],
)
def test_a_program_runs_without_asking(
    deps: UserDeps, code: str | None, script_path: str | None
) -> None:
    """What a program changes is approved afterwards, through apply_changes."""
    python_tools.validate_run_python(
        _context(deps, "interactive"), code=code, script_path=script_path
    )


async def test_tmp_writes_skip_approval(deps: UserDeps) -> None:
    """`/tmp` is the run's own, so only a workspace write asks the user."""

    def context(mode: str) -> RunContext[UserDeps]:
        return _context(deps, mode)

    async def write(mode: str, file_path: str) -> None:
        await validate_document_write(context(mode), file_path=file_path, content="x")

    folder = tmp_dir(settings.data_dir, "c1")
    folder.mkdir(parents=True)
    (folder / "a.json").write_text("{}")
    (folder / "state.json").write_text("x")
    await write("interactive", "/tmp/state.json")
    await validate_document_edit(
        context("interactive"), file_path="/tmp/state.json", edits=[TextEdit("x", "y")]
    )
    await validate_document_deletions(context("interactive"), paths=["/tmp/a.json"])

    # No workspace name is reserved for it, so a `tmp` folder there is a document's.
    with pytest.raises(ApprovalRequired) as exc:
        await write("interactive", "@team/tmp/run.py")

    assert [create["path"] for create in _asked(exc)["creates"]] == [
        "@team/tmp/run.py"
    ]

    # A traversal reaches neither another conversation's folder nor a document.
    with pytest.raises(ModelRetry, match="not accessible"):
        await write("interactive", "/tmp/../c2/state.json")

    with pytest.raises(ModelRetry, match="not accessible"):
        await validate_document_write(
            _context(replace(deps, conversation_id=None), "interactive"),
            file_path="/tmp/state.json",
            content="x",
        )

    await write("read", "/tmp/state.json")
    await write("write", "~/notes/report.md")


async def test_an_edit_that_cannot_apply_is_refused_before_the_approval(
    deps: UserDeps,
) -> None:
    workspace_dir = deps.store.workspace_dir(settings.data_dir)
    (workspace_dir / "notes.md").write_text("alpha beta")
    edits = [TextEdit("alpha", "gamma"), TextEdit("missing", "x")]

    with pytest.raises(ModelRetry, match="Edit 2 of 2"):
        await validate_document_edit(
            _context(deps, "interactive"), file_path="~/notes.md", edits=edits
        )

    with pytest.raises(ApprovalRequired) as exc:
        await validate_document_edit(
            _context(deps, "interactive"), file_path="~/notes.md", edits=edits[:1]
        )

    assert _asked(exc)["updates"][0]["path"] == "~/notes.md"

    # Approved, or in a mode that asks nothing, the commit is what refuses.
    await validate_document_edit(
        _context(deps, "interactive", approved=True), file_path="~/notes.md", edits=edits
    )


def test_write_tools_gate_every_call_rather_than_the_tool() -> None:
    validators = {
        "write_document": validate_document_write,
        "edit_document": validate_document_edit,
        "move_documents": validate_document_moves,
        "delete_documents": validate_document_deletions,
        "apply_changes": validate_apply_changes,
    }

    assert set(write_toolset.tools) == set(validators)

    for name, validator in validators.items():
        tool = write_toolset.tools[name]
        assert tool.args_validator is validator
        assert tool.requires_approval is False


@pytest.fixture()
def documents(deps: UserDeps, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two documents in the personal workspace, with every row lookup stubbed."""

    async def _no_metadata(*_args: object, **_kwargs: object) -> None:
        return None

    async def _no_rows(*_args: object, **_kwargs: object) -> dict[object, object]:
        return {}

    monkeypatch.setattr(db_documents, "get_entry_metadata", _no_metadata)
    monkeypatch.setattr(db_documents, "get_entries_metadata", _no_rows)
    workspace_dir = deps.store.workspace_dir(settings.data_dir)
    (workspace_dir / "a.md").write_text("a")
    (workspace_dir / "b.md").write_text("b")

    return workspace_dir


async def test_a_batch_of_moves_is_one_approval_of_the_planner_s_summary(
    deps: UserDeps, documents: Path
) -> None:
    (documents / "old").mkdir()
    (documents / "old" / "c.md").write_text("c")
    moves = [
        DocumentMove("~/a.md", "~/notes/a.md"),
        DocumentMove("~/b.md", "@team/b.md"),
        DocumentMove("~/old", "~/archive"),
    ]

    with pytest.raises(ApprovalRequired) as exc:
        await validate_document_moves(_context(deps, "interactive"), moves=moves)

    def move(source: str, destination: str, *, is_dir: bool = False) -> dict[str, object]:
        return {
            "source": source,
            "destination": destination,
            "is_dir": is_dir,
            "replaces": False,
        }

    assert _asked(exc) == {
        "creates": [],
        "updates": [],
        "paths": {
            "moves": [
                move("~/a.md", "~/notes/a.md"),
                move("~/b.md", "@team/b.md"),
                move("~/old", "~/archive", is_dir=True),
            ],
            "deletes": [],
        },
        "mkdirs": [],
    }


async def test_overlapping_moves_are_refused_before_the_approval(
    deps: UserDeps, documents: Path
) -> None:
    moves = [DocumentMove("~/a.md", "~/c.md"), DocumentMove("~/b.md", "~/c.md")]

    with pytest.raises(ModelRetry, match="more than one change"):
        await validate_document_moves(_context(deps, "interactive"), moves=moves)


async def test_a_batch_of_deletions_is_one_approval(
    deps: UserDeps, documents: Path
) -> None:
    with pytest.raises(ApprovalRequired) as exc:
        await validate_document_deletions(
            _context(deps, "interactive"), paths=["~/a.md", "~/b.md"]
        )

    assert _asked(exc)["paths"]["deletes"] == ["~/a.md", "~/b.md"]


_STAGED = Changeset((Move("~/a.md", "~/notes/a.md"), Write("~/notes/a.md", "A")))
_SUMMARY = ChangesetSummary(
    updates=(FileDiff("~/notes/a.md", "-a\n+A\n"),),
    paths=PathChanges(moves=(PathMove("~/a.md", "~/notes/a.md"),)),
)


@pytest.fixture()
def planned(monkeypatch: pytest.MonkeyPatch) -> list[Changeset[Location]]:
    """Plan every changeset as valid with :data:`_SUMMARY`, recording each."""
    calls: list[Changeset[Location]] = []

    async def _plan(changeset: Changeset[Location], **_options: object) -> PlannedChangeset:
        calls.append(changeset)

        return PlannedChangeset(changeset, _SUMMARY)

    monkeypatch.setattr(gateway, "plan_changeset", _plan)

    return calls


async def test_a_program_s_changes_are_staged_unless_nobody_need_approve(
    deps: UserDeps,
    planned: list[Changeset[Location]],
    routed: list[tuple[str, str]],
) -> None:
    folder = tmp_dir(settings.data_dir, "c1")
    with_tmp = Changeset((*_STAGED.operations, Write("/tmp/state.json", "{}")))

    staged = await changeset_committer(deps)(with_tmp)
    applied = await changeset_committer(replace(deps, mode="write"))(_STAGED)

    assert isinstance(staged, PendingChanges)
    assert staged.summary == _SUMMARY
    assert await staging.load_staged("u", staged.changeset_id) == _STAGED
    assert await staging.load_staged("someone-else", staged.changeset_id) is None
    assert (folder / "state.json").read_text() == "{}"
    assert applied == AppliedChanges(("written", "written"))
    assert len(planned) == 1
    assert routed == [
        ("user:u", "a.md"),
        ("user:u", "notes/a.md"),
        ("user:u", "notes/a.md"),
    ]

    # A program that changed only `/tmp` has nothing to approve.
    only_tmp = Changeset((Write("/tmp/state.json", "[]"),))
    assert await changeset_committer(replace(deps, mode="read"))(only_tmp) is None
    assert (folder / "state.json").read_text() == "[]"


async def test_apply_changes_asks_once_then_applies_and_forgets(
    deps: UserDeps,
    planned: list[Changeset[Location]],
    routed: list[tuple[str, str]],
) -> None:
    changeset_id = await staging.stage("u", _STAGED)

    with pytest.raises(ApprovalRequired) as exc:
        await validate_apply_changes(
            _context(deps, "interactive"), changeset_id=changeset_id
        )

    assert _asked(exc) == {
        "creates": [],
        "updates": [{"path": "~/notes/a.md", "diff": "-a\n+A\n"}],
        "paths": {
            "moves": [
                {
                    "source": "~/a.md",
                    "destination": "~/notes/a.md",
                    "is_dir": False,
                    "replaces": False,
                }
            ],
            "deletes": [],
        },
        "mkdirs": [],
    }

    approved = _context(deps, "interactive", approved=True)
    await validate_apply_changes(approved, changeset_id=changeset_id)
    result = await _apply_changes(deps)(changeset_id)

    assert result.data == "written\nwritten"
    assert len(planned) == 1
    assert routed == [("user:u", "a.md"), ("user:u", "notes/a.md"), ("user:u", "notes/a.md")]

    with pytest.raises(ToolRetry, match="No staged changeset"):
        await _apply_changes(deps)(changeset_id)


async def test_a_stale_changeset_asks_for_a_new_run(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _stale(_changeset: Changeset[Location], **_options: object) -> PlannedChangeset:
        raise HTTPException(status_code=409, detail="'~/a.md' changed")

    monkeypatch.setattr(gateway, "plan_changeset", _stale)
    changeset_id = await staging.stage("u", _STAGED)

    with pytest.raises(ModelRetry, match="changed. Run the program again"):
        await validate_apply_changes(
            _context(deps, "interactive"), changeset_id=changeset_id
        )


async def test_unapproved_changesets_are_discarded(data_dir: Path) -> None:
    _ = data_dir
    approved = await staging.stage("u", _STAGED)
    denied = await staging.stage("u", _STAGED)
    foreign = await staging.stage("someone-else", _STAGED)
    response = ModelResponse(
        parts=[
            ToolCallPart("apply_changes", {"changeset_id": approved}, "call-1"),
            ToolCallPart("apply_changes", {"changeset_id": denied}, "call-2"),
            ToolCallPart("apply_changes", {"changeset_id": foreign}, "call-3"),
        ]
    )

    await discard_unapproved_changes("u", response, {"call-1"})

    assert await staging.load_staged("u", approved) == _STAGED
    assert await staging.load_staged("u", denied) is None
    assert await staging.load_staged("someone-else", foreign) == _STAGED
