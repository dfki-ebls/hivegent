"""Tests for a conversation's `/tmp`, one folder per conversation outside every workspace."""

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic_monty import AsyncMonty

from hivegent import staging
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.write import changeset_committer, program_paths
from hivegent.auth import User
from hivegent.changes import Changeset, ChangesetSummary, Delete, Move, Write
from hivegent.config import settings
from hivegent.mcp.tools import documents as mcp_documents
from hivegent.mcp.tools import mutations as mcp_mutations
from hivegent.server.operations import reads
from hivegent.server.routes import documents
from hivegent.store import Casebase
from hivegent.text import DecodedText
from hivegent.tmp import save_result, sweep_tmp, tmp_dir, tmp_root, tmp_search_path
from hivegent.tools.base import ToolRetry
from hivegent.tools.changeset import PendingChanges
from hivegent.tools.documents import DocumentRead, ListDocumentsTool, ReadDocumentTool
from hivegent.tools.grep import GrepTool
from hivegent.tools.jq import JqTool
from hivegent.tools.mutations import write_changeset
from hivegent.tools.python import RunPythonTool
from hivegent.tools.table import QueryTableTool
from hivegent.tools.workspace_os import ChangesetLimits
from hivegent.workspace import Gateway, Location, PlannedChangeset
from hivegent.workspace import changeset as gateway
from tests.helpers import LIMITS


def _gateway(folder: Path) -> Gateway:
    return Gateway((tmp_root(folder),))


@pytest.mark.parametrize(
    ("conversation_id", "filepath", "status"),
    [
        ("c1", "/tmp/report.py", 200),
        ("c1", "/tmp/missing.py", 404),
        ("c2", "/tmp/report.py", 404),
        ("c1", "/tmp/escape.py", 404),
    ],
)
async def test_the_document_route_reads_an_owned_conversations_tmp(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    conversation_id: str,
    filepath: str,
    status: int,
) -> None:
    for cid in ("c1", "c2"):
        folder = tmp_dir(data_dir, cid)
        folder.mkdir(parents=True)
        (folder / "report.py").write_text(f"print('{cid}')")

    (tmp_dir(data_dir, "c1") / "escape.py").symlink_to(tmp_dir(data_dir, "c2") / "report.py")

    async def owned(user_id: str, cid: str) -> bool:
        return user_id == "u" and cid == "c1"

    monkeypatch.setattr(documents, "conversation_exists", owned)
    user = User(id="u")

    if status != 200:
        with pytest.raises(HTTPException) as exc:
            await documents.get_document_content(filepath, user, conversation_id)

        assert exc.value.status_code == status

        return

    response = await documents.get_document_content(filepath, user, conversation_id)
    assert response.body == b"print('c1')"


async def test_the_read_tools_reach_tmp(data_dir: Path) -> None:
    folder = tmp_dir(data_dir, "c1")
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("needle\n")
    (folder / "data.json").write_text('{"total": 41}')
    (folder / "rows.csv").write_text("x,y\n1,2\n")
    paths = UserDeps("u", Casebase.for_user("u"), "interactive", "c1").search_paths()

    read = await ReadDocumentTool(paths=paths)([DocumentRead("/tmp/notes.md")])
    grep = await GrepTool(paths=paths)("needle")
    listing = await ListDocumentsTool(paths=paths)("/tmp")
    jq = await JqTool(paths=paths)(["/tmp/data.json"], ".total + 1")
    table = await QueryTableTool(paths=paths)(["/tmp/rows.csv"], ["SELECT x + y AS s FROM t"])

    assert "needle" in read.text
    assert "/tmp/notes.md" in grep.text
    assert all(f"/tmp/{name}" in listing.text for name in ("data.json", "rows.csv"))
    assert "42" in jq.text
    assert "3" in table.text


async def test_mcp_has_no_tmp(data_dir: Path) -> None:
    """A caller without a conversation is told which roots it does have."""
    store = Casebase.for_user("u")
    tmp_dir(data_dir, "c1").mkdir(parents=True)
    paths = mcp_documents._search_paths(store, ())

    with pytest.raises(ToolRetry, match=r"not found\. This tool addresses ~;"):
        await ReadDocumentTool(paths=paths)([DocumentRead("/tmp/state.json")])

    with pytest.raises(ToolRetry, match=r"not accessible\. This tool addresses ~;"):
        _ = write_changeset(mcp_mutations._paths(store), "/tmp/state.json", "x")


async def test_a_write_past_the_quota_is_refused_unless_it_frees_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.tmp, "max_bytes", 10)
    (tmp_path / "a.txt").write_text("x" * 8)

    with pytest.raises(HTTPException, match="over the 10 B"):
        _ = await _gateway(tmp_path).plan(Changeset((Write("/tmp/b.txt", "x" * 8),)))

    assert not (tmp_path / "b.txt").exists()

    _ = await _gateway(tmp_path).apply(
        Changeset((Delete("/tmp/a.txt"), Write("/tmp/b.txt", "y" * 8)))
    )
    assert [path.name for path in tmp_path.iterdir()] == ["b.txt"]


async def test_concurrent_spills_check_and_write_under_one_lock(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.tmp, "max_bytes", 6_000)
    folder = tmp_dir(data_dir, "c1")
    write = gateway._write_workspace_file

    def slow_write(staging: Path, name: str, data: bytes) -> Path:
        time.sleep(0.02)

        return write(staging, name, data)

    monkeypatch.setattr(gateway, "_write_workspace_file", slow_write)
    _ = await asyncio.gather(
        *(save_result(folder, f"result-{index}.txt", "x" * 4_000) for index in range(2))
    )

    files = [path for path in folder.rglob("*") if path.is_file()]
    assert len(files) == 1
    assert sum(path.stat().st_size for path in files) == 4_000


async def test_spills_preserve_working_files_and_recheck_planned_write_quotas(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.tmp, "max_bytes", 6_000)
    folder = tmp_dir(data_dir, "c1")
    (folder / "results").mkdir(parents=True)
    working = folder / "results" / "computed.csv"
    _ = working.write_bytes(b"x" * 1_000)
    state = Changeset((Write("/tmp/state.txt", "y" * 2_000),))
    _ = await _gateway(folder).plan(state)

    first = await save_result(folder, "first.txt", "a" * 4_000)
    second = await save_result(folder, "second.txt", "b" * 4_000)

    assert working.read_bytes() == b"x" * 1_000
    assert first == "/tmp/.tool-results/first.txt"
    assert second == "/tmp/.tool-results/second.txt"
    assert not (folder / ".tool-results" / "first.txt").exists()
    assert (folder / ".tool-results" / "second.txt").read_bytes() == b"b" * 4_000

    with pytest.raises(HTTPException, match="over the"):
        _ = await _gateway(folder).apply(state)

    assert not (folder / "state.txt").exists()


@pytest.mark.parametrize("operation", [
    Write("/tmp/.tool-results/new.txt", "x"),
    Move("/tmp/.tool-results", "/tmp/moved"),
    Move("/tmp/state.txt", "/tmp/.tool-results/new.txt"),
])
async def test_ordinary_changes_cannot_modify_the_spill_cache(
    data_dir: Path, operation: Write[str] | Move[str]
) -> None:
    folder = tmp_dir(data_dir, "c1")
    _ = await save_result(folder, "saved.txt", "saved")
    _ = (folder / "state.txt").write_text("state")

    with pytest.raises(HTTPException, match="can be read but not changed"):
        _ = await _gateway(folder).plan(Changeset((operation,)))

    assert (folder / ".tool-results" / "saved.txt").read_text() == "saved"


async def test_a_program_can_read_but_cannot_write_the_spill_cache(data_dir: Path) -> None:
    root = tmp_search_path(data_dir, "c1")
    _ = await save_result(root.path, "saved.txt", "saved")

    async with AsyncMonty(min_processes=1) as pool:
        result = await RunPythonTool(
            pool=pool, paths=(root,), writable=(root,), changeset_limits=LIMITS
        )("""
from pathlib import Path
cached = Path('/tmp/.tool-results/saved.txt')
try:
    cached.write_text('changed')
except Exception:
    pass
cached.read_text()
""")

    assert result.data.result == "'saved'"
    assert (root.path / ".tool-results" / "saved.txt").read_text() == "saved"


async def test_a_program_renames_within_tmp_without_rewriting(data_dir: Path) -> None:
    """A directory and a binary move as they are, and a plain file may change its extension."""
    deps = UserDeps("u", Casebase.for_user("u"), "read", "c1")
    folder = tmp_dir(data_dir, "c1")
    (folder / "plots").mkdir(parents=True)
    (folder / "plots" / "a.png").write_bytes(b"\x89PNG\x00")
    (folder / "old.bin").write_bytes(b"\x00\xff")

    async with AsyncMonty(min_processes=1) as pool:
        tool = RunPythonTool(
            pool=pool,
            paths=deps.search_paths(),
            writable=program_paths(deps),
            commit=changeset_committer(deps),
            changeset_limits=LIMITS,
        )
        await tool(
            "from pathlib import Path\n"
            "Path('/tmp/plots').rename('/tmp/figures')\n"
            "Path('/tmp/old.bin').rename('/tmp/figures/new.dat')\n"
        )

    assert (folder / "figures" / "a.png").read_bytes() == b"\x89PNG\x00"
    assert (folder / "figures" / "new.dat").read_bytes() == b"\x00\xff"
    assert sorted(path.name for path in folder.iterdir()) == ["figures"]


async def test_a_program_writes_tmp_once_it_succeeds_and_stages_the_workspace(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned: list[Changeset[Location]] = []

    async def plan(changeset: Changeset[Location], **_options: object) -> PlannedChangeset:
        planned.append(changeset)

        return PlannedChangeset(changeset, ChangesetSummary())

    monkeypatch.setattr(gateway, "plan_changeset", plan)
    deps = UserDeps("u", Casebase.for_user("u"), "interactive", "c1")
    folder = tmp_dir(data_dir, "c1")
    folder.mkdir(parents=True)
    (folder / "state.json").write_text("1")
    program = (
        "from pathlib import Path\n"
        "n = int(Path('/tmp/state.json').read_text()) + 1\n"
        "Path('/tmp/state.json').write_text(str(n))\n"
        "Path('~/report.md').write_text(f'run {n}')\n"
    )
    limits = ChangesetLimits(max_operations=10, max_deletes=10, max_chars=1_000)

    async with AsyncMonty(min_processes=1) as pool:
        tool = RunPythonTool(
            pool=pool,
            paths=deps.search_paths(),
            writable=program_paths(deps),
            commit=changeset_committer(deps),
            changeset_limits=limits,
        )
        result = await tool(program)

        with pytest.raises(ToolRetry, match="ZeroDivisionError"):
            await tool(program + "1 / 0")

        monkeypatch.setattr(settings.tmp, "max_bytes", 1)

        with pytest.raises(ToolRetry, match="over the"):
            await replace(tool, commit=changeset_committer(deps))(
                f"{program}Path('/tmp/more.txt').write_text('xx')"
            )

    assert (folder / "state.json").read_text() == "2"
    changeset = result.data.changeset
    assert isinstance(changeset, PendingChanges)
    assert await staging.load_staged("u", changeset.changeset_id) == Changeset(
        (Write("~/report.md", "run 2", "create"),)
    )
    # The run past the cap was planned too, and refused once its `/tmp` half applied.
    assert len(planned) == 2
    assert not (deps.store.workspace_path(data_dir) / "report.md").exists()


async def test_the_sweep_drops_expired_and_orphaned_folders(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orphan is kept for a sweep interval, since a first turn has no row yet."""

    async def live(ids: list[str]) -> set[str]:
        assert ids == ["orphan"]

        return {"fresh", "expired"} & set(ids)

    monkeypatch.setattr("hivegent.tmp.existing_conversation_ids", live)
    now = time.time()
    ages = {
        "fresh": now,
        "expired": now - settings.tmp.ttl_hours * 3600 - 60,
        "orphan": now - settings.tmp.sweep_interval_hours * 3600 - 60,
        "first-turn": now,
    }

    for name, touched in ages.items():
        folder = tmp_dir(data_dir, name)
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}")

        for path in (folder, folder / "state.json"):
            os.utime(path, (touched, touched))

    assert await sweep_tmp() == 2
    assert sorted(path.name for path in (data_dir / "tmp").iterdir()) == [
        "first-turn",
        "fresh",
    ]


async def test_the_sweep_keeps_an_orphan_used_during_its_lookup(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_dir(data_dir, "first-turn")
    folder.mkdir(parents=True)
    touched = time.time() - settings.tmp.sweep_interval_hours * 3600 - 60
    os.utime(folder, (touched, touched))

    async def live(ids: list[str]) -> set[str]:
        assert ids == ["first-turn"]
        (folder / "active.txt").write_text("working")

        return set()

    monkeypatch.setattr("hivegent.tmp.existing_conversation_ids", live)

    assert await sweep_tmp() == 0
    assert (folder / "active.txt").read_text() == "working"


@pytest.mark.parametrize(("directory", "status"), [(False, 404), (True, 400)])
async def test_a_file_replaced_during_a_read_returns_its_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, status: int
) -> None:
    target = tmp_path / "document.md"
    target.write_text("hello")
    read = reads.read_text_file

    def concurrent_read(path: Path) -> DecodedText | None:
        path.unlink()

        if directory:
            path.mkdir()

        return read(path)

    monkeypatch.setattr(reads, "read_text_file", concurrent_read)

    with pytest.raises(HTTPException) as exc:
        await reads.get_file_response(lambda: target, "~/document.md")

    assert exc.value.status_code == status
