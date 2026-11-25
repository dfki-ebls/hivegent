"""The built-in local connector against a temporary directory."""

import os
from pathlib import Path

import pytest

from hivegent.sources.local import Local, LocalOptions


def connector(path: Path, **options: int | bool) -> Local:
    return Local(options=LocalOptions(path=path, require_mount=False, **options))


async def test_a_listing_names_every_file_and_skips_symlinks(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "sub" / "b.md").write_text("bb")
    (tmp_path / "link.md").symlink_to(tmp_path / "a.md")
    (tmp_path / "linked").symlink_to(tmp_path / "sub")

    delta = await connector(tmp_path).delta(None)

    assert sorted(item.path for item in delta.items) == ["a.md", "sub/b.md"]
    assert delta.complete
    assert await connector(tmp_path).read(delta.items[0]) in {b"a", b"bb"}


async def test_a_file_swapped_after_the_listing_for_a_symlink_or_fifo_is_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "share"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("a")
    (root / "sub" / "b.md").write_text("b")
    (root / "c.md").write_text("c")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "b.md").write_text("secret")
    source = connector(root)
    items = {item.path: item for item in (await source.delta(None)).items}

    (root / "a.md").unlink()
    (root / "a.md").symlink_to(tmp_path / "secret" / "b.md")
    (root / "sub" / "b.md").unlink()
    (root / "sub").rmdir()
    (root / "sub").symlink_to(tmp_path / "secret")
    (root / "c.md").unlink()
    os.mkfifo(root / "c.md")

    for path in ("a.md", "sub/b.md", "c.md"):
        with pytest.raises(OSError):
            _ = await source.read(items[path])


async def test_a_file_beyond_the_size_cap_is_refused(tmp_path: Path) -> None:
    (tmp_path / "big.md").write_text("12345")
    source = connector(tmp_path, max_file_size_bytes=4)

    [item] = (await source.delta(None)).items

    with pytest.raises(ValueError, match="exceeds"):
        await source.read(item)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
async def test_an_unreadable_folder_leaves_the_listing_incomplete(
    tmp_path: Path,
) -> None:
    (tmp_path / "ok.md").write_text("a")
    (tmp_path / "locked").mkdir()
    (tmp_path / "locked").chmod(0)

    try:
        delta = await connector(tmp_path).delta(None)
    finally:
        (tmp_path / "locked").chmod(0o700)

    assert [item.path for item in delta.items] == ["ok.md"]
    assert delta.refused == ["locked"]
    assert not delta.complete


async def test_a_path_that_is_no_mount_point_fails_the_listing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a mount point"):
        await Local(options=LocalOptions(path=tmp_path)).delta(None)
