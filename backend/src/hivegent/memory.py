"""Persistent per-user memory storage.

Memory is stored as a single markdown file that is overwritten entirely
on each save.
"""

from pathlib import Path

from .config import settings

__all__ = ["clear_memory", "load_memory", "save_memory"]


def _memory_path(user_id: str) -> Path:
    """Return the path to a user's memory file.

    Args:
        user_id: The user identifier.

    Returns:
        Absolute path to ``data/users/<user_id>/memory.md``.
    """
    return settings.get_user_dir(user_id) / "memory.md"


def load_memory(user_id: str) -> str | None:
    """Load the user's memory content.

    Args:
        user_id: The user identifier.

    Returns:
        The memory content, or ``None`` if no memory file exists.
    """
    path = _memory_path(user_id)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def save_memory(user_id: str, content: str) -> None:
    """Overwrite the user's memory file.

    Args:
        user_id: The user identifier.
        content: The full markdown content to write.
    """
    path = _memory_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def clear_memory(user_id: str) -> bool:
    """Delete the user's memory file.

    Args:
        user_id: The user identifier.

    Returns:
        ``True`` if the file existed and was deleted, ``False`` otherwise.
    """
    path = _memory_path(user_id)
    if path.is_file():
        path.unlink()
        return True
    return False
