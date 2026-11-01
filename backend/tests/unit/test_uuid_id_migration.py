"""The folder move of the migration that rewrites conversation ids into UUIDs."""

import importlib.util
from pathlib import Path
from types import ModuleType

from hivegent.config import is_canonical_uuid

_MIGRATION = next(
    (Path(__file__).parents[2] / "src/hivegent/migrations/versions").glob(
        "*_e3f4a5b6c7d8_uuid_conversation_ids.py"
    )
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("uuid_conversation_ids", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_folders_move_below_their_user_under_their_new_id_once(data_dir: Path) -> None:
    """A rerun after a failed rewrite finds every folder already in place."""
    migration = _load()
    legacy, kept = "V1StGXR8_Z5jdHi6B-myT", "0b6c4c9e-4f9a-4f0e-9d7a-3f6e2b1c8a90"
    root = data_dir / "conversations"

    for old in (legacy, kept):
        (root / old / "tmp").mkdir(parents=True)

    conversations = [(legacy, "alice"), (kept, "bob")]
    migration._move_folders(conversations)
    migration._move_folders(conversations)

    new = migration._new_id(legacy)
    assert is_canonical_uuid(new) and new == migration._new_id(legacy)
    assert migration._new_id(kept) == kept
    assert sorted(p.relative_to(root).as_posix() for p in root.glob("*/*")) == sorted(
        [f"alice/{new}", f"bob/{kept}"]
    )
