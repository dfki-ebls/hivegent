"""Unit tests for the roots the agent's write tools reach and how each commits.

A workspace's changes go through the changeset gateway and its approval, ``/tmp``'s straight into the conversation's folder.
"""

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from exar.helpers import get_name
from exar.tools import Retry
from pydantic_ai import DeferredToolRequests, RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import hivegent.agents.tools.python as python_tools
from hivegent import staging
from hivegent.agents.common import Hooked, UserDeps, probe_context
from hivegent.agents.tools.write import (
    program_writes,
    retain_awaited_changes,
    validate_apply_changes,
    validate_document_deletions,
    validate_document_edit,
    validate_document_moves,
    validate_document_write,
    write_tools,
    write_toolset,
)
from hivegent.config import settings
from hivegent.store import GlobalScope, GroupScope, User, UserScope
from hivegent.tmp import tmp_dir
from hivegent.tools.base import Direct, ToolFunc
from hivegent.tools.changes import (
    ChangeReason,
    Changeset,
    ChangesetSummary,
    FileDiff,
    Move,
    PathMove,
    TextEdit,
    Write,
)
from hivegent.tools.changeset import AppliedChanges, PendingChanges
from hivegent.tools.entries import ContentStat
from hivegent.tools.mutations import DocumentMove
from hivegent.types import DocumentFilter
from tests.helpers import FakeIndex, doc, owned, run_context


def _tool(deps: UserDeps, name: str) -> ToolFunc:
    """The write tool *name* of a run over *deps*."""
    tools = [
        item.func if isinstance(item, Hooked) else item
        for item in write_tools(run_context(deps))
    ]

    return {get_name(tool): tool for tool in tools}[name]


@pytest.fixture()
def deps(data_dir: Path, index: FakeIndex) -> UserDeps:
    """Deps with one writable and one read-only group, over the in-memory index."""
    _ = data_dir, index
    user = User(
        "u", read_groups=frozenset({"archive"}), write_groups=frozenset({"team"})
    )

    return UserDeps(user=user, mode="interactive", conversation=owned("c1", user))


async def test_writes_to_the_addressed_workspace(
    deps: UserDeps, index: FakeIndex
) -> None:
    tool = _tool(deps, "write_document")

    for path in ("@team/notes/a.md", "~/notes/a.md", "notes/b.md"):
        _ = await tool(path, "hi")

    assert sorted(index.rows) == [
        ("group:team", "notes/a"),
        ("user:u", "notes/a"),
        ("user:u", "notes/b"),
    ]


async def test_tmp_is_written_directly_and_a_program_there_is_pointed_at_run_python(
    deps: UserDeps, index: FakeIndex
) -> None:
    """A `/tmp` `.py` is written to be run, so the receipt says how, edit included."""
    folder = tmp_dir(settings.data_dir, owned("c1"))
    pointer = "Run it with run_python's `script_path='/tmp/report.py'`."

    written = (
        await _tool(deps, "write_document")("/tmp/report.py", "print(1)")
    ).metadata
    edited = (
        await _tool(deps, "edit_document")("/tmp/report.py", [TextEdit("1", "2")])
    ).metadata

    assert written == f"Wrote 8 characters to '/tmp/report.py'. {pointer}"
    assert edited == f"Replaced 1 occurrence in '/tmp/report.py'. {pointer}"
    assert (folder / "report.py").read_text() == "print(2)"

    with pytest.raises(Retry, match="changed since it was read"):
        await _tool(deps, "edit_document")(
            "/tmp/report.py", [TextEdit("2", "3")], "stale"
        )

    # Anything else carries no pointer back to run_python.
    assert (await _tool(deps, "write_document")("/tmp/rows.json", "[]")).metadata == (
        "Wrote 2 characters to '/tmp/rows.json'."
    )

    # Moves and deletes reach `/tmp` too, and land at once.
    _ = await _tool(deps, "move_documents")(
        [DocumentMove("/tmp/rows.json", "/tmp/old/rows.json")]
    )
    assert (folder / "old" / "rows.json").read_text() == "[]"
    _ = await _tool(deps, "delete_documents")(["/tmp/old/rows.json"])
    assert not (folder / "old" / "rows.json").exists()

    with pytest.raises(Retry, match="only one of them is written directly"):
        await _tool(deps, "move_documents")(
            [DocumentMove("/tmp/report.py", "~/report.py")]
        )

    assert index.rows == {}


async def test_tmp_refuses_what_would_outgrow_it(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap counts what a write replaces, so a full folder may still shrink."""
    monkeypatch.setattr(settings.tmp, "max_bytes", 4)
    tool = _tool(deps, "write_document")
    _ = await tool("/tmp/a.txt", "1234")

    with pytest.raises(Retry, match="over the"):
        await tool("/tmp/b.txt", "5")

    _ = await tool("/tmp/a.txt", "12")
    _ = await tool("/tmp/b.txt", "34")


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("@nope/a.md", r"addresses ~, @team, /tmp; give the full path"),
        ("@archive/notes/a.md", "not accessible"),
    ],
)
async def test_an_unknown_or_read_only_root_is_refused(
    deps: UserDeps, index: FakeIndex, path: str, message: str
) -> None:
    """A sigil-led path is never read as relative, so it cannot land under ``~``."""
    with pytest.raises(Retry, match=message):
        await _tool(deps, "write_document")(path, "hi")

    assert index.rows == {}


def test_run_python_paths_end_with_the_conversation_s_tmp(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    filtered = replace(
        deps,
        document_filters={
            scope: DocumentFilter(excluded=frozenset({doc("other.md")}))
            for scope in (
                GlobalScope(),
                GroupScope("archive"),
                GroupScope("team"),
                UserScope("u"),
            )
        },
    )
    paths = filtered.search_paths()

    assert all(not path.path.exists() for path in paths)
    *workspaces, tmp = paths
    assert len(workspaces) == 4
    # A skill is no document, so only `load_skill` and `run_python` reach one.
    skills = filtered.program_paths()[len(paths) :]
    assert [path.mount.render("") for path in skills] == [
        "/skills/~",
        "/skills/@archive",
        "/skills/@team",
    ]

    for path in workspaces:
        assert path.filter_func is not None
        assert path.filter_func("report.md")
        assert not path.filter_func("other.md")

    assert tmp.mount.render("") == "/tmp"
    assert tmp.policy == Direct(reserved=".tool-results")
    assert python_tools._environ(filtered)["TMPDIR"] == "/tmp"
    # The writable workspaces and `/tmp`, never a skill library.
    assert len(program_writes(filtered).paths) == 3

    # Read mode, or a withheld apply_changes, changes no workspace and keeps `/tmp`.
    assert program_writes(replace(filtered, mode="read")).paths == (tmp,)

    monkeypatch.setattr(settings.tools, "disabled", ["apply_changes"])
    assert program_writes(filtered).paths == (tmp,)


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
        ({"code": "1 + 1"}, None),
        ({"script_path": "/tmp/run.py"}, None),
        ({"code": "", "script_path": "/tmp/run.py"}, None),
        ({"code": "1 + 1", "script_path": " "}, None),
    ],
)
async def test_a_program_is_refused_only_when_empty_or_ambiguous(
    deps: UserDeps, arguments: dict[str, str | None], message: str | None
) -> None:
    """What a valid program changes is approved afterwards, through apply_changes."""
    tool = python_tools.python_toolset.build(probe_context()).tools["run_python"]
    validated = tool.function_schema.validator.validate_python(arguments)
    assert getattr(tool.args_validator, "__wrapped__", None) is (
        python_tools.validate_run_python
    )
    validate = python_tools.validate_run_python(
        _context(deps, "interactive"), **validated
    )

    if message is None:
        await validate

        return

    with pytest.raises(Retry, match=message):
        await validate


async def test_tmp_writes_skip_approval(deps: UserDeps) -> None:
    """`/tmp` is the run's own, so only a workspace write asks the user."""

    def context(mode: str) -> RunContext[UserDeps]:
        return _context(deps, mode)

    async def write(mode: str, file_path: str) -> None:
        await validate_document_write(context(mode), file_path=file_path, content="x")

    folder = tmp_dir(settings.data_dir, owned("c1"))
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

    assert [create["path"] for create in _asked(exc)["creates"]] == ["@team/tmp/run.py"]

    # A traversal reaches neither another conversation's folder nor a document.
    with pytest.raises(Retry, match="not accessible"):
        await write("interactive", "/tmp/../c2/state.json")

    with pytest.raises(Retry, match="not accessible"):
        await validate_document_write(
            _context(replace(deps, conversation=None), "interactive"),
            file_path="/tmp/state.json",
            content="x",
        )

    await write("read", "/tmp/state.json")
    await write("write", "~/notes/report.md")


async def test_an_edit_that_cannot_apply_is_refused_before_the_approval(
    deps: UserDeps,
) -> None:
    workspace_dir = deps.user.home.workspace_dir()
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
        _context(deps, "interactive", approved=True),
        file_path="~/notes.md",
        edits=edits,
    )


def test_write_tools_gate_every_call_rather_than_the_tool() -> None:
    validators = {
        "write_document": validate_document_write,
        "edit_document": validate_document_edit,
        "move_documents": validate_document_moves,
        "delete_documents": validate_document_deletions,
        "apply_changes": validate_apply_changes,
    }

    tools = write_toolset.build(probe_context()).tools

    assert set(tools) == set(validators)

    for name, validator in validators.items():
        tool = tools[name]
        assert getattr(tool.args_validator, "__wrapped__", None) is validator
        assert tool.requires_approval is False


@pytest.fixture()
def documents(deps: UserDeps) -> Path:
    """Two documents in the personal workspace."""
    workspace_dir = deps.user.home.workspace_dir()
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

    def move(
        source: str, destination: str, *, is_dir: bool = False
    ) -> dict[str, object]:
        return {
            "source": source,
            "destination": destination,
            "is_dir": is_dir,
            "replaces": False,
        }

    assert _asked(exc)["moves"] == [
        move("~/a.md", "~/notes/a.md"),
        move("~/b.md", "@team/b.md"),
        move("~/old", "~/archive", is_dir=True),
    ]


async def test_a_move_into_a_group_workspace_asks_only_in_interactive_mode(
    deps: UserDeps, documents: Path
) -> None:
    _ = documents
    moves = [DocumentMove("~/b.md", "@team/b.md")]

    with pytest.raises(ApprovalRequired):
        await validate_document_moves(_context(deps, "interactive"), moves=moves)

    await validate_document_moves(_context(deps, "write"), moves=moves)


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

    assert _asked(exc)["deletes"] == ["~/a.md", "~/b.md"]


_STAGED = Changeset((Move("~/a.md", "~/notes/a.md"), Write("~/notes/a.md", "A")))
_SUMMARY = ChangesetSummary(
    updates=(
        FileDiff(
            "~/notes/a.md",
            "--- ~/notes/a.md\n+++ ~/notes/a.md\n@@ -1 +1 @@\n-a\n"
            "\\ No newline at end of file\n+A\n\\ No newline at end of file\n",
        ),
    ),
    moves=(PathMove("~/a.md", "~/notes/a.md"),),
)


def _files(workspace_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace_dir).as_posix(): path.read_text()
        for path in sorted(workspace_dir.rglob("*"))
        if path.is_file()
    }


async def test_a_program_s_changes_are_staged_unless_nobody_need_approve(
    deps: UserDeps, documents: Path
) -> None:
    folder = tmp_dir(settings.data_dir, owned("c1"))
    with_tmp = Changeset((*_STAGED.operations, Write("/tmp/state.json", "{}")))

    staged = await program_writes(deps).commit(with_tmp)

    # The `/tmp` half waits with the rest, so it lands whole or not at all.
    assert isinstance(staged, PendingChanges)
    assert (staged.summary.moves, staged.held) == (_SUMMARY.moves, 1)
    assert (
        await staging.load_staged(owned("c1", deps.user), staged.changeset_id)
        == with_tmp
    )
    assert (
        await staging.load_staged(owned("c2", deps.user), staged.changeset_id) is None
    )
    assert not (folder / "state.json").exists()
    assert _files(documents) == {"a.md": "a", "b.md": "b"}

    applied = await program_writes(replace(deps, mode="write")).commit(_STAGED)

    assert isinstance(applied, AppliedChanges) and len(applied.reports) == 2
    assert _files(documents) == {"notes/a.md": "A", "b.md": "b"}

    # A program that changed only `/tmp` has nothing to approve.
    only_tmp = Changeset((Write("/tmp/state.json", "[]"),))
    assert await program_writes(replace(deps, mode="read")).commit(only_tmp) is None
    assert (folder / "state.json").read_text() == "[]"


async def test_apply_changes_asks_once_then_applies_and_forgets(
    deps: UserDeps, documents: Path
) -> None:
    reason = ChangeReason("remove", 2, "A duplicate of case 1.")
    staged = replace(_STAGED, reasons=(reason,))
    changeset_id = await staging.stage(owned("c1", deps.user), staged)

    with pytest.raises(ApprovalRequired) as exc:
        await validate_apply_changes(
            _context(deps, "interactive"), changeset_id=changeset_id
        )

    asked = _asked(exc)
    assert asked["updates"] == [
        {"path": update.path, "diff": update.diff} for update in _SUMMARY.updates
    ]
    assert asked["reasons"] == [
        {"action": "remove", "key": 2, "reason": "A duplicate of case 1."}
    ]

    approved = _context(deps, "interactive", approved=True)
    await validate_apply_changes(approved, changeset_id=changeset_id)
    await _tool(deps, "apply_changes")(changeset_id)

    assert _files(documents) == {"notes/a.md": "A", "b.md": "b"}

    with pytest.raises(Retry, match="No staged changeset"):
        await _tool(deps, "apply_changes")(changeset_id)


async def test_a_staged_changeset_reaches_nothing_the_current_run_hides(
    deps: UserDeps, documents: Path
) -> None:
    _ = documents
    changeset_id = await staging.stage(owned("c1", deps.user), _STAGED)
    hidden = replace(
        deps,
        document_filters={
            UserScope("u"): DocumentFilter(excluded=frozenset({doc("a.md")}))
        },
    )

    with pytest.raises(ModelRetry, match="not accessible"):
        await validate_apply_changes(
            _context(hidden, "interactive"), changeset_id=changeset_id
        )


async def test_a_stale_changeset_asks_for_a_new_run(
    deps: UserDeps, documents: Path
) -> None:
    basis = ContentStat.from_path(documents / "a.md")
    staged = Changeset((Write("~/a.md", "A", "replace", basis),))
    changeset_id = await staging.stage(owned("c1", deps.user), staged)
    (documents / "a.md").write_text("changed meanwhile")

    with pytest.raises(
        ModelRetry, match="changed since it was read.*Run the program again"
    ):
        await validate_apply_changes(
            _context(deps, "interactive"), changeset_id=changeset_id
        )


async def test_only_awaited_or_new_changesets_stay_staged(data_dir: Path) -> None:
    mine, other = owned("c1"), owned("c2")
    awaited = await staging.stage(mine, _STAGED)
    orphan = await staging.stage(mine, _STAGED)
    foreign = await staging.stage(other, _STAGED)
    pending = DeferredToolRequests(
        approvals=[ToolCallPart("apply_changes", {"changeset_id": awaited}, "call-1")]
    )

    # A turn that started after everything was staged, then one that started before it.
    after, before = math.inf, 0.0

    await retain_awaited_changes(mine, pending, after)

    assert await staging.load_staged(mine, awaited) == _STAGED
    assert await staging.load_staged(mine, orphan) is None
    assert await staging.load_staged(other, foreign) == _STAGED

    fresh = await staging.stage(mine, _STAGED)
    await retain_awaited_changes(mine, None, before)

    assert await staging.load_staged(mine, fresh) == _STAGED

    await retain_awaited_changes(mine, None, after)

    assert not (mine.folder(data_dir) / staging.CHANGESETS_FOLDER).exists()
