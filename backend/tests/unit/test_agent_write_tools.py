"""Unit tests for the workspaces the agent's mutating tools can reach.

The write tools search the personal workspace plus the groups the user may
write to, and route each accepted path back to the workspace that claimed it,
so a path copied out of a listing lands where it says it does.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import hivegent.agents.tools.compute as compute_tools
import hivegent.agents.tools.write as write_tools
from hivegent import workspace
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.write import (
    _edit_document,
    _write_document,
    output_sink,
    validate_document_write,
    validate_output_path,
    validate_output_write,
    write_document,
    write_toolset,
)
from hivegent.store import Casebase
from hivegent.tools.base import ToolRetry
from hivegent.types import DocumentFilter


@pytest.fixture()
def deps(data_dir: Path) -> UserDeps:
    """Deps with one writable and one read-only group."""
    _ = data_dir
    return UserDeps(
        user_id="u",
        store=Casebase.for_user("u"),
        mode="interactive",
        group_stores=(Casebase.for_group("team"), Casebase.for_group("archive")),
        write_group_stores=(Casebase.for_group("team"),),
    )


@pytest.fixture()
def routed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record the ``(store_key, local_path)`` each write is routed to."""
    calls: list[tuple[str, str]] = []

    async def _write(store: Casebase, safe: str, *_args: object, **_kw: object) -> str:
        calls.append((store.store_key, safe))
        return "written"

    monkeypatch.setattr(workspace, "write_document_text", _write)
    return calls


async def test_writes_to_the_addressed_workspace(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    tool = write_document(deps)

    assert (await tool("@team/notes/a.md", "hi")).data == "written"
    assert (await tool("~/notes/a.md", "hi")).data == "written"
    assert routed == [("group:team", "notes/a.md"), ("user:u", "notes/a.md")]


async def test_stored_program_is_pointed_at_run_python(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    """A `.scratch/` `.py` is written to be run, so the receipt says how."""
    tool = _write_document(deps)

    program = (await tool("~/.scratch/report.py", "print(1)")).data
    document = (await tool("~/.scratch/rows.json", "[]")).data

    assert (
        program
        == "written Run it with run_python's `script_path='~/.scratch/report.py'`."
    )
    assert document == "written"
    assert len(routed) == 2


async def test_the_pointer_survives_the_edit_that_repairs_the_program(
    deps: UserDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rerun is the point of the edit, so the same hint rides both tools."""

    async def _edit(_store: Casebase, _safe: str, *_a: object, **_kw: object) -> str:
        return "edited"

    monkeypatch.setattr(workspace, "edit_document_text", _edit)
    tool = _edit_document(deps)

    result = (await tool("~/.scratch/report.py", "1", "2")).data

    assert result.endswith(
        "Run it with run_python's `script_path='~/.scratch/report.py'`."
    )


async def test_a_declared_output_is_not_a_stored_program(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    """run_python's own commit carries no pointer back to run_python."""
    sink = output_sink(deps)
    assert sink is not None

    assert (await sink("~/.scratch/report.py", "print(1)")).data == "written"
    assert len(routed) == 1


async def test_unprefixed_path_is_refused_with_the_roots_named(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    """Nothing is implied by context, so the refusal says what to write instead."""
    tool = write_document(deps)

    with pytest.raises(ToolRetry, match=r"addresses ~, @team; give the full path"):
        await tool("notes/a.md", "hi")

    assert routed == []


async def test_refuses_a_group_the_user_may_only_read(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    tool = write_document(deps)

    with pytest.raises(ToolRetry, match="not accessible"):
        await tool("@archive/notes/a.md", "hi")

    assert routed == []


def test_run_python_paths_are_lazy_and_never_hide_scratch(
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
    monkeypatch.setattr(compute_tools, "get_monty_pool", lambda: pool)

    tool = compute_tools._run_python(filtered)

    assert tool.pool is pool
    assert all(not path.path.exists() for path in tool.resolved_paths)
    assert len(tool.resolved_paths) == 3
    for path in tool.resolved_paths:
        assert path.filter_func is not None
        assert path.filter_func(".scratch/run.json")
        assert path.filter_func("report.md")
        assert not path.filter_func("other.md")

    assert tool.writer is not None
    assert len(tool.writer.resolved_paths) == 2
    for path in tool.writer.resolved_paths:
        assert path.filter_func is not None
        assert path.filter_func("notes/.scratch/state.json")
        assert not path.filter_func("other.md")


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


def test_agent_output_write_approval_depends_on_mode(deps: UserDeps) -> None:
    def context(mode: str, *, approved: bool = False) -> RunContext[UserDeps]:
        return _context(deps, mode, approved=approved)

    with pytest.raises(ApprovalRequired):
        validate_output_path(context("interactive"), output_path="~/output.txt")

    validate_output_path(
        context("interactive", approved=True), output_path="~/output.txt"
    )
    validate_output_path(context("write"), output_path="~/output.txt")

    with pytest.raises(ModelRetry, match="unavailable"):
        validate_output_path(context("read"), output_path="~/output.txt")


def test_run_python_output_accepts_arbitrary_text_suffix(deps: UserDeps) -> None:
    context = _context(deps, "write")

    assert (
        compute_tools.compute_toolset.tools["run_python"].args_validator
        is validate_output_write
    )
    validate_output_write(context, output_path="~/result.csv")
    validate_output_write(context, output_path="~/report.md")


def test_scratch_writes_skip_approval_without_lifting_the_mode_gate(
    deps: UserDeps,
) -> None:
    """Run state is the run's own, so only a document write asks the user."""

    def context(mode: str) -> RunContext[UserDeps]:
        return _context(deps, mode)

    validate_document_write(context("interactive"), file_path="~/.scratch/state.json")
    validate_document_write(
        context("interactive"), file_path="@team/notes/.scratch/run.py"
    )
    validate_output_write(context("interactive"), output_path="~/.scratch/rows.json")

    with pytest.raises(ApprovalRequired):
        validate_document_write(context("interactive"), file_path="~/notes/report.md")

    # A traversal cannot carry a document out of the scratch spelling it hides behind.
    with pytest.raises(ApprovalRequired):
        validate_document_write(
            context("interactive"), file_path="~/.scratch/../report.md"
        )

    with pytest.raises(ModelRetry, match="unavailable"):
        validate_document_write(context("read"), file_path="~/.scratch/state.json")

    validate_document_write(context("write"), file_path="~/notes/report.md")


def test_write_tools_gate_every_call_rather_than_the_tool() -> None:
    for name in ("write_document", "edit_document"):
        tool = write_toolset.tools[name]
        assert tool.args_validator is validate_document_write
        assert tool.requires_approval is False


def test_a_binary_output_is_refused_before_the_approval(deps: UserDeps) -> None:
    """The suffix decides first, so no approval and no run is spent on it."""
    context = _context(deps, "interactive")

    with pytest.raises(ModelRetry, match="binary format"):
        validate_output_write(context, output_path="~/sheet.xlsx")


def test_a_scratch_output_answers_to_no_format(deps: UserDeps) -> None:
    """Scratch is the run's own bytes, so neither the format nor the user gates it."""
    context = _context(deps, "interactive")

    validate_output_write(context, output_path="~/.scratch/state.parquet")


def test_a_move_puts_both_ends_in_one_approval(deps: UserDeps) -> None:
    context = _context(deps, "interactive")

    with pytest.raises(ApprovalRequired) as exc:
        write_tools.validate_document_move(
            context, file_path="~/old.md", destination="~/notes/new.md"
        )

    assert exc.value.metadata == {
        "file_path": "~/old.md",
        "destination": "~/notes/new.md",
    }


def test_move_and_delete_are_registered_with_their_gates() -> None:
    tools = write_tools.write_toolset.tools

    assert tools["move_document"].args_validator is write_tools.validate_document_move
    assert tools["delete_document"].args_validator is validate_document_write
