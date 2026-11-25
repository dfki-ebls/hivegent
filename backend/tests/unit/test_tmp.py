"""Tests for a conversation's `/tmp`, one folder per conversation outside every workspace."""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import ANY
from uuid import UUID

import pytest
from exar.tools import Retry
from exar.tools.batch import unpack
from exar.tools.files import DocumentRead
from fastapi import HTTPException
from pydantic_monty import AsyncMonty

from hivegent import staging
from hivegent.agents import invoke_agent_tool
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.explore import (
    grep,
    jq,
    list_documents,
    query_table,
    read_document,
)
from hivegent.agents.tools.write import program_writes
from hivegent.config import settings
from hivegent.conversation_files import reset_conversation_files, sweep_conversations
from hivegent.db.conversations import OwnedConversation, claim_conversation
from hivegent.server.operations import reads
from hivegent.server.routes import documents
from hivegent.store import User
from hivegent.tmp import save_result, tmp_dir, tmp_root
from hivegent.tools.base import mounts_of
from hivegent.tools.changes import (
    ChangeRefused,
    Changeset,
    ChangesetSummary,
    Delete,
    Move,
    Write,
)
from hivegent.tools.changeset import PendingChanges
from hivegent.tools.python import run_python
from hivegent.tools.workspace_os import ChangesetLimits
from hivegent.workspace import Gateway, Location, PlannedChangeset
from hivegent.workspace.changeset import apply as changeset_apply
from hivegent.workspace.changeset import gateway
from tests.helpers import LIMITS, label_id, owned


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
        folder = tmp_dir(data_dir, owned(cid))
        folder.mkdir(parents=True)
        (folder / "report.py").write_text(f"print('{cid}')")

    (tmp_dir(data_dir, owned("c1")) / "escape.py").symlink_to(
        tmp_dir(data_dir, owned("c2")) / "report.py"
    )

    async def lookup(user: User, cid: UUID) -> OwnedConversation | None:
        return owned("c1", user) if user.id == "u" and cid == label_id("c1") else None

    monkeypatch.setattr(documents, "owned_conversation", lookup)
    user = User(id="u")

    if status != 200:
        with pytest.raises(HTTPException) as exc:
            await documents.get_document_content(
                filepath, user, label_id(conversation_id)
            )

        assert exc.value.status_code == status

        return

    response = await documents.get_document_content(
        filepath, user, label_id(conversation_id)
    )
    assert response.body == b"print('c1')"


async def test_the_agent_tools_bind_the_mounts_of_the_run(data_dir: Path) -> None:
    folder = tmp_dir(data_dir, owned("c1"))
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("needle\n")
    deps = UserDeps(User("u"), "interactive", owned("c1"))

    output = unpack(await invoke_agent_tool("grep", {"pattern": "needle"}, deps))

    assert output.text == "/tmp/notes.md\n1:needle"


async def test_the_read_tools_reach_tmp(data_dir: Path) -> None:
    folder = tmp_dir(data_dir, owned("c1"))
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("needle\n")
    (folder / "data.json").write_text('{"total": 41}')
    (folder / "rows.csv").write_text("x,y\n1,2\n")
    paths = UserDeps(User("u"), "interactive", owned("c1")).search_paths()

    read = await read_document(mounts_of(paths))([DocumentRead("/tmp/notes.md")])
    found = await grep(mounts_of(paths))("needle")
    listing = await list_documents(mounts_of(paths))("/tmp")
    filtered = await jq(mounts_of(paths))(["/tmp/data.json"], ".total + 1")
    table = await query_table(mounts_of(paths))(
        ["/tmp/rows.csv"], ["SELECT x + y AS s FROM t"]
    )

    assert "needle" in read.return_value
    assert "/tmp/notes.md" in found.return_value
    assert all(
        f"/tmp/{name}" in listing.return_value for name in ("data.json", "rows.csv")
    )
    assert "42" in filtered.return_value
    assert "3" in table.return_value


async def test_a_write_past_the_quota_is_refused_unless_it_frees_room(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.tmp, "max_bytes", 10)
    folder = data_dir / "tmp"
    folder.mkdir()
    (folder / "a.txt").write_text("x" * 8)

    with pytest.raises(ChangeRefused, match="over the 10 B"):
        _ = await _gateway(folder).plan(Changeset((Write("/tmp/b.txt", "x" * 8),)))

    assert not (folder / "b.txt").exists()

    _ = await _gateway(folder).apply(
        Changeset((Delete("/tmp/a.txt"), Write("/tmp/b.txt", "y" * 8)))
    )
    assert [path.name for path in folder.iterdir()] == ["b.txt"]


async def test_concurrent_spills_check_and_write_under_one_lock(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.tmp, "max_bytes", 6_000)
    folder = tmp_dir(data_dir, owned("c1"))
    write = changeset_apply._write_workspace_file

    def slow_write(staging: Path, name: str, data: bytes) -> Path:
        time.sleep(0.02)

        return write(staging, name, data)

    monkeypatch.setattr(changeset_apply, "_write_workspace_file", slow_write)
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
    folder = tmp_dir(data_dir, owned("c1"))
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

    with pytest.raises(ChangeRefused, match="over the"):
        _ = await _gateway(folder).apply(state)

    assert not (folder / "state.txt").exists()


@pytest.mark.parametrize(
    "operation",
    [
        Write("/tmp/.tool-results/new.txt", "x"),
        Move("/tmp/.tool-results", "/tmp/moved"),
        Move("/tmp/state.txt", "/tmp/.tool-results/new.txt"),
    ],
)
async def test_ordinary_changes_cannot_modify_the_spill_cache(
    data_dir: Path, operation: Write[str] | Move[str]
) -> None:
    folder = tmp_dir(data_dir, owned("c1"))
    _ = await save_result(folder, "saved.txt", "saved")
    _ = (folder / "state.txt").write_text("state")

    with pytest.raises(ChangeRefused, match="can be read but not changed"):
        _ = await _gateway(folder).plan(Changeset((operation,)))

    assert (folder / ".tool-results" / "saved.txt").read_text() == "saved"


async def test_a_program_can_read_but_cannot_write_the_spill_cache(
    data_dir: Path,
) -> None:
    root = tmp_root(tmp_dir(data_dir, owned("c1"))).search_path
    _ = await save_result(root.path, "saved.txt", "saved")

    async with AsyncMonty(min_processes=1) as pool:
        result = await run_python(
            (root,), pool=lambda: pool, writable=(root,), changeset_limits=LIMITS
        )("""
from pathlib import Path
cached = Path('/tmp/.tool-results/saved.txt')
try:
    cached.write_text('changed')
except Exception:
    pass
cached.read_text()
""")

    assert result.metadata.result == "'saved'"
    assert (root.path / ".tool-results" / "saved.txt").read_text() == "saved"


async def test_a_program_renames_within_tmp_without_rewriting(data_dir: Path) -> None:
    """A directory and a binary move as they are, and a plain file may change its extension."""
    deps = UserDeps(User("u"), "read", owned("c1"))
    folder = tmp_dir(data_dir, owned("c1"))
    (folder / "plots").mkdir(parents=True)
    (folder / "plots" / "a.png").write_bytes(b"\x89PNG\x00")
    (folder / "old.bin").write_bytes(b"\x00\xff")

    async with AsyncMonty(min_processes=1) as pool:
        tool = run_python(
            deps.search_paths(),
            pool=lambda: pool,
            writable=program_writes(deps).paths,
            commit=program_writes(deps).commit,
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


async def test_a_program_stages_its_tmp_changes_with_the_workspace_s(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned: list[Changeset[Location]] = []

    async def plan(
        changeset: Changeset[Location], **_options: object
    ) -> PlannedChangeset:
        planned.append(changeset)

        return PlannedChangeset(changeset, ChangesetSummary())

    monkeypatch.setattr(gateway, "plan_changeset", plan)
    deps = UserDeps(User("u"), "interactive", owned("c1"))
    folder = tmp_dir(data_dir, owned("c1"))
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
        tool = run_python(
            deps.search_paths(),
            pool=lambda: pool,
            writable=program_writes(deps).paths,
            commit=program_writes(deps).commit,
            changeset_limits=limits,
        )
        result = await tool(program)

        with pytest.raises(Retry, match="ZeroDivisionError"):
            await tool(program + "1 / 0")

    # The `/tmp` half is staged with the workspace's, against the stat it read.
    assert (folder / "state.json").read_text() == "1"
    changeset = result.metadata.changeset
    assert isinstance(changeset, PendingChanges)
    assert await staging.load_staged(owned("c1"), changeset.changeset_id) == Changeset(
        (
            Write("~/report.md", "run 2", "create"),
            Write("/tmp/state.json", "2", "replace", ANY),
        )
    )
    assert len(planned) == 1
    assert not (deps.user.home.path / "report.md").exists()


def _age(*paths: Path, touched: float) -> None:
    for path in paths:
        os.utime(path, (touched, touched))


def _stale() -> float:
    return time.time() - settings.conversations.ttl_hours * 3600 - 60


@pytest.mark.usefixtures("memory_store")
async def test_the_sweep_removes_a_folder_only_once_all_of_it_is_idle(
    data_dir: Path,
) -> None:
    """A stale `/tmp` beside a fresh changeset stays, and the other way round."""
    root = OwnedConversation.user_root(data_dir, owned("idle").owner)

    for name in ("idle", "fresh-tmp", "fresh-changeset"):
        folder = tmp_dir(data_dir, owned(name))
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}")
        _ = await staging.stage(owned(name), Changeset(()))
        whole = owned(name).folder(data_dir)
        _age(*whole.rglob("*"), whole, touched=_stale())

    os.utime(tmp_dir(data_dir, owned("fresh-tmp")) / "state.json")
    changesets = owned("fresh-changeset").folder(data_dir) / staging.CHANGESETS_FOLDER
    os.utime(next(changesets.iterdir()))

    assert await sweep_conversations() == 1
    assert sorted(path.name for path in root.iterdir()) == sorted(
        str(label_id(name)) for name in ("fresh-changeset", "fresh-tmp")
    )


@pytest.mark.usefixtures("memory_store")
async def test_each_user_has_their_own_folder_and_the_sweep_drops_an_empty_one(
    data_dir: Path,
) -> None:
    """A run still writing after its conversation's deletion stays its user's."""
    alice, bob = owned("c1", User("alice")), owned("c1", User("bob"))
    assert alice.folder(data_dir) != bob.folder(data_dir)

    folder = tmp_dir(data_dir, alice)
    folder.mkdir(parents=True)
    _age(folder, folder.parent, touched=_stale())

    assert await sweep_conversations() == 1
    assert not OwnedConversation.user_root(data_dir, alice.owner).exists()


@pytest.mark.usefixtures("memory_store")
async def test_the_sweep_keeps_the_staged_changesets_of_an_existing_conversation(
    data_dir: Path,
) -> None:
    """Only `/tmp` goes, since a pending approval can still apply its changeset."""
    conversation = owned("kept")
    assert await claim_conversation(conversation) is not None
    tmp = tmp_dir(data_dir, conversation)
    tmp.mkdir(parents=True)
    (tmp / "state.json").write_text("{}")
    changeset_id = await staging.stage(conversation, Changeset(()))
    folder = conversation.folder(data_dir)
    _age(*folder.rglob("*"), folder, touched=_stale())

    assert await sweep_conversations() == 1
    assert not tmp.exists()
    assert await staging.load_staged(conversation, changeset_id) == Changeset(())


async def test_a_reset_drops_every_conversation_folder(data_dir: Path) -> None:
    tmp_dir(data_dir, owned("c1")).mkdir(parents=True)
    _ = await staging.stage(owned("c2"), Changeset(()))

    await reset_conversation_files()

    assert not (data_dir / "conversations").exists()


@pytest.mark.usefixtures("memory_store")
async def test_the_sweep_keeps_a_folder_written_while_it_waits_on_the_lock(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_dir(data_dir, owned("c1"))
    folder.mkdir(parents=True)
    _age(folder.parent, folder, touched=_stale())

    class WriteFirst(asyncio.Lock):
        async def __aenter__(self) -> None:
            (folder / "active.txt").write_text("working")
            await super().__aenter__()

    monkeypatch.setattr(
        "hivegent.conversation_files.root_lock", lambda _root: WriteFirst()
    )

    assert await sweep_conversations() == 0
    assert (folder / "active.txt").read_text() == "working"


@pytest.mark.parametrize(("directory", "status"), [(False, 404), (True, 400)])
async def test_a_file_replaced_during_a_read_returns_its_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, status: int
) -> None:
    target = tmp_path / "document.md"
    target.write_text("hello")
    read = Path.read_bytes

    def concurrent_read(path: Path) -> bytes:
        path.unlink()

        if directory:
            path.mkdir()

        return read(path)

    monkeypatch.setattr(Path, "read_bytes", concurrent_read)

    with pytest.raises(HTTPException) as exc:
        await reads.get_file_response(lambda: target, "~/document.md")

    assert exc.value.status_code == status


@pytest.mark.parametrize(
    ("name", "content", "media_type", "attachment"),
    [
        ("main.rs", b"fn main() {}", "text/plain", False),
        (
            "figure.svg",
            b"<svg xmlns='http://www.w3.org/2000/svg'/>",
            "image/svg+xml",
            True,
        ),
        ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png", True),
        ("blob.bin", b"\x00\xff\x00\xfe", "application/octet-stream", True),
    ],
)
async def test_a_file_is_served_as_text_only_when_its_type_allows(
    tmp_path: Path, name: str, content: bytes, media_type: str, attachment: bool
) -> None:
    target = tmp_path / name
    target.write_bytes(content)

    response = await reads.get_file_response(lambda: target, f"~/{name}")

    assert response.media_type == media_type
    assert ("content-disposition" in response.headers) is attachment
    assert response.headers["x-content-type-options"] == "nosniff"
