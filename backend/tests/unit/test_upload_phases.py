"""Uploads commit through the changeset gateway: converted first, landed whole.

The conversion runs before any lock and writes nothing, so a failure there or
in the file swap leaves a prior entry as it was, and a change made to what an
upload replaces while it converts refuses the commit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from cbrkit.io import COMPANION_SUFFIXES
from PIL.PngImagePlugin import PngInfo

from hivegent import workspace
from hivegent.store import Workspace
from hivegent.tools.changes import ChangeRefused
from hivegent.tools.entries import Part
from hivegent.workspace import IndexingFailed, prepare
from tests.helpers import FakeIndex, png_bytes


def _png_with_metadata() -> bytes:
    """A PNG carrying an ancillary tEXt chunk that sanitisation would strip."""
    info = PngInfo()
    info.add_text("Comment", "x" * 256)

    return png_bytes(info)


@dataclass(slots=True)
class Caption:
    """What the vision model answers, which a test may change, fail, or act on."""

    text: str | None = "caption\n"
    fails: bool = False
    meanwhile: Callable[[], None] | None = None

    async def __call__(self, *_args: object) -> str | None:
        if self.fails:
            raise RuntimeError("conversion failed")

        if self.meanwhile:
            self.meanwhile()

        return self.text


@pytest.fixture()
def caption(monkeypatch: pytest.MonkeyPatch) -> Caption:
    """The vision model of every image, answering ``caption``."""
    answer = Caption()
    monkeypatch.setattr(prepare, "_build_image_description", answer)

    return answer


@pytest.fixture()
def workspace_dir(user_store: Workspace, caption: Caption, index: FakeIndex) -> Path:
    """The workspace of *user_store*, with its rows in the shared index and its images captioned."""
    _ = caption, index

    return user_store.workspace_dir()


async def test_image_upload_reports_stage_and_stores_sidecar_and_original_verbatim(
    user_store: Workspace, workspace_dir: Path
) -> None:
    """The stored original keeps its ancillary chunks, sanitising is the model's job."""
    raw, ctx = _png_with_metadata(), Mock()

    await workspace.upload(user_store, "photo.png", raw, ctx=ctx)

    ctx.set_stage.assert_any_call("Generating image description")
    assert (workspace_dir / "photo.png").read_bytes() == raw
    assert (workspace_dir / "photo.md").read_text() == "caption\n"


async def test_undescribed_image_replaces_entry_without_sidecar(
    user_store: Workspace, workspace_dir: Path, caption: Caption
) -> None:
    await workspace.upload(user_store, "photo.png", png_bytes())
    caption.text = None
    raw = _png_with_metadata()
    await workspace.upload(user_store, "photo.png", raw, overwrite=True)

    assert (workspace_dir / "photo.png").read_bytes() == raw
    assert not (workspace_dir / "photo.md").exists()


async def test_failed_index_leaves_files_for_reconcile(
    user_store: Workspace, workspace_dir: Path, index: FakeIndex
) -> None:
    index.failing = {"photo"}

    with pytest.raises(IndexingFailed):
        await workspace.upload(user_store, "photo.png", png_bytes())

    assert (workspace_dir / "photo.png").exists()
    assert (workspace_dir / "photo.md").exists()


async def test_markdown_and_companion_original_land_as_one_entry(
    user_store: Workspace, workspace_dir: Path, index: FakeIndex
) -> None:
    await workspace.upload(
        user_store, "report.md", b"# Report\n", companion=("report.pdf", b"%PDF")
    )

    assert (workspace_dir / "report.md").read_text() == "# Report\n"
    assert (workspace_dir / "report.pdf").read_bytes() == b"%PDF"
    assert [e.original_path for e in index.indexed] == ["report.pdf"]


async def test_upload_into_assets_is_refused(
    user_store: Workspace, workspace_dir: Path
) -> None:
    """An `.assets` payload would be disowned by its entry, so no upload lands there."""
    with pytest.raises(ChangeRefused) as exc:
        await workspace.upload(user_store, "notes/report.assets/fig1.md", b"x")

    assert exc.value.status_code == 400
    assert not (workspace_dir / "notes").exists()


async def test_replace_restores_entry_when_staged_install_fails(
    user_store: Workspace, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await workspace.upload(user_store, "photo.png", b"old image")
    assets = workspace_dir / "photo.assets"
    assets.mkdir()
    (assets / "old.png").write_bytes(b"old asset")
    real_replace = Path.replace
    failed = False

    def fail_new_original(self: Path, target: Path) -> Path:
        nonlocal failed
        if not failed and self.name == "photo.png" and self.parent.parent.name == "new":
            failed = True
            raise OSError("simulated replacement failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_new_original)

    with pytest.raises(OSError, match="simulated replacement failure"):
        await workspace.replace_original(
            user_store, "photo.png", b"new image", new_filename="photo.png"
        )

    assert (workspace_dir / "photo.png").read_bytes() == b"old image"
    assert (workspace_dir / "photo.md").read_text() == "caption\n"
    assert (assets / "old.png").read_bytes() == b"old asset"


async def test_replace_with_another_suffix_supersedes_the_original(
    user_store: Workspace, workspace_dir: Path
) -> None:
    await workspace.upload(user_store, "photo.png", png_bytes())

    await workspace.replace_original(
        user_store, "photo.md", b"new image", new_filename="Photo.JPG"
    )

    assert not (workspace_dir / "photo.png").exists()
    assert (workspace_dir / "photo.jpg").read_bytes() == b"new image"
    assert (workspace_dir / "photo.md").read_text() == "caption\n"


@pytest.mark.parametrize(("name", "kept"), [("notes.txt", True), ("notes.ini", False)])
async def test_an_upload_over_an_entry_keeps_or_takes_every_companion(
    user_store: Workspace, workspace_dir: Path, name: str, kept: bool
) -> None:
    """Over its own original the cbrkit companions stay and the payload is converted afresh, over another suffix all go."""
    await workspace.upload(user_store, "notes.txt", b"old")
    companions = [
        Part("notes", kind, ".txt").path for kind in ("assets", *COMPANION_SUFFIXES)
    ]

    for path in companions:
        if Part.of(path).kind == "assets":
            (workspace_dir / path).mkdir()
        else:
            (workspace_dir / path).write_text("{}")

    await workspace.upload(user_store, name, b"new", overwrite=True)

    assert [(workspace_dir / path).exists() for path in companions] == [
        kept and Part.of(path).kind != "assets" for path in companions
    ]
    assert (workspace_dir / name).read_bytes() == b"new"


async def test_reconvert_refuses_an_original_changed_meanwhile(
    user_store: Workspace, workspace_dir: Path, caption: Caption
) -> None:
    """A change to the original during the conversion wins."""
    await workspace.upload(user_store, "photo.png", png_bytes())
    caption.text = "new caption\n"

    def edit() -> None:
        _ = (workspace_dir / "photo.png").write_bytes(b"edited meanwhile")

    caption.meanwhile = edit

    with pytest.raises(ChangeRefused) as exc:
        await workspace.reconvert(user_store, "photo.md")

    assert exc.value.status_code == 409
    assert (workspace_dir / "photo.png").read_bytes() == b"edited meanwhile"
    assert (workspace_dir / "photo.md").read_text() == "caption\n"


async def test_upload_dates_the_entry_and_reconvert_keeps_it(
    user_store: Workspace, workspace_dir: Path
) -> None:
    modified_at = datetime(2020, 5, 17, 12, 30, tzinfo=UTC)
    await workspace.upload(
        user_store, "photo.png", png_bytes(), modified_at=modified_at
    )
    await workspace.reconvert(user_store, "photo.md")

    for name in ("photo.png", "photo.md"):
        assert (workspace_dir / name).stat().st_mtime == modified_at.timestamp()


async def test_reprocess_failure_preserves_existing_entry(
    user_store: Workspace, workspace_dir: Path, caption: Caption
) -> None:
    """A failed reconversion leaves the prior entry intact, never destroyed.

    Every workspace write happens at commit, so a failure (or cancel) during
    the lock-free conversion cannot strip the original or its description,
    the guarantee that makes a cancellable reconvert or replace safe.
    """
    await workspace.upload(user_store, "photo.png", png_bytes())
    original = (workspace_dir / "photo.png").read_bytes()

    caption.fails = True

    with pytest.raises(RuntimeError):
        await workspace.replace_original(
            user_store, "photo.png", b"new bytes", new_filename="photo.png"
        )

    assert (workspace_dir / "photo.png").read_bytes() == original
    assert (workspace_dir / "photo.md").exists()
