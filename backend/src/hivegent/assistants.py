"""Assistants by scope: where each lives, who sees it, and how a run applies one.

An assistant (:class:`~hivegent.types.AssistantSpec`) is a name, a
description, instructions, skills to load, and limits that only narrow a run:
tools it withholds, the mode it caps, and documents it adds to the selection.
It belongs to a scope like a skill does:

* the global ones are ``<name>.json`` files in the operator's
  ``assistants.dir``, which nobody changes through Hivegent, and
* a group's or a user's are ``<data_dir>/assistants/<owner>/<name>.json``,
  written only through the REST API, a group's by its writers, so no agent
  tool can reach or change one.

A user sees them in the order user, groups, global, and a chat names one by
its reference (:func:`~hivegent.libraries.library_ref`), its scope's prefix
and its name, so no scope can stand in for another's.  Only the operator's
instructions are instructions to the model, everyone else's reach it as a
fenced tool result (``agents/tools/assistant.py``).
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from cbrkit.io import atomic_write
from fastapi import HTTPException
from pydantic import ValidationError

from .config import settings
from .files import remove_path
from .libraries import (
    LibraryScope,
    Listed,
    library_ref,
    resolve_ref,
    visible,
    writable_library,
)
from .shared.l10n import Localized
from .shared.skills import valid_skill_name
from .store import GlobalScope, GroupScope, Scope, User, UserScope
from .types import MODE_ORDER, AssistantSpec, RunChoices
from .workspace_events import announce_library_changed

__all__ = [
    "Assistant",
    "apply_assistant",
    "assistant_library",
    "delete_assistant",
    "global_assistants",
    "remove_assistants_root",
    "resolve_assistant",
    "save_assistant",
    "visible_assistants",
]

logger = logging.getLogger(__name__)

_SUFFIX = ".json"


def _not_found(name: str) -> Localized[str]:
    return Localized(
        en=f"No assistant named {name!r} here",
        de=f"Hier gibt es keinen Assistenten „{name}“",
    )


@dataclass(frozen=True, slots=True)
class Assistant:
    """An assistant some scope holds."""

    scope: Scope
    name: str
    spec: AssistantSpec

    @property
    def trusted(self) -> bool:
        """Whether the operator wrote it, so its instructions carry their authority."""
        return isinstance(self.scope, GlobalScope)

    @property
    def ref(self) -> str:
        """The reference a chat names it by, such as ``~/legal``."""
        return library_ref(self)


def _root() -> Path:
    return settings.data_dir / "assistants"


def assistant_library(scope: LibraryScope) -> Path:
    """Where Hivegent keeps the assistants of *scope*."""
    return _root() / scope.key


def _directory(scope: Scope) -> Path | None:
    """Where the assistants of *scope* lie, ``None`` without an operator directory."""
    match scope:
        case GlobalScope():
            return settings.assistants.dir

        case GroupScope() | UserScope():
            return assistant_library(scope)


def _parse(scope: Scope, path: Path) -> Assistant:
    """The assistant *path* holds, named by its stem.

    Raises:
        ValueError: For a file that is no valid assistant.
    """
    if not valid_skill_name(path.stem):
        raise ValueError(f"Invalid assistant name: {path.name}")

    try:
        spec = AssistantSpec.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError(f"Invalid assistant {path}: {exc}") from exc

    return Assistant(scope, path.stem, spec)


def _files(directory: Path) -> list[Path]:
    """The assistant files in *directory*, by name, a staged one left out."""
    return sorted(
        path
        for path in directory.glob(f"*{_SUFFIX}")
        if path.is_file() and not path.name.startswith(".")
    )


def _assistants_of(scope: Scope) -> list[Assistant]:
    """Every valid assistant *scope* holds, by name, an invalid file logged and skipped."""
    directory = _directory(scope)

    if directory is None or not directory.is_dir():
        return []

    assistants: list[Assistant] = []

    for path in _files(directory):
        try:
            assistants.append(_parse(scope, path))
        except ValueError:
            logger.warning("Skipping the invalid assistant %s", path, exc_info=True)

    return assistants


def global_assistants() -> list[Assistant]:
    """The operator's assistants, each file parsed, for the boot check.

    Raises:
        ValueError: For a file that is no valid assistant.
    """
    directory = settings.assistants.dir

    if directory is None:
        return []

    return [_parse(GlobalScope(), path) for path in _files(directory)]


def visible_assistants(user: User) -> list[Listed[Assistant]]:
    """Every assistant *user* sees, their own first, then each group's, then global.

    Blocking, it reads every file.
    """
    return visible(user, _assistants_of)


def _named(scope: Scope, name: str) -> Assistant | None:
    """The valid assistant *name* of *scope*, ``None`` for none, an invalid file logged and skipped.  Blocking."""
    directory = _directory(scope)

    if directory is None or not valid_skill_name(name):
        return None

    path = directory / f"{name}{_SUFFIX}"

    if not path.is_file():
        return None

    try:
        return _parse(scope, path)
    except ValueError:
        logger.warning("Skipping the invalid assistant %s", path, exc_info=True)

        return None


async def resolve_assistant(user: User, ref: str) -> Assistant | None:
    """The assistant *ref* names among those *user* sees, ``None`` for none.

    Reads only the one file *ref* names, and an invalid one is skipped as
    :func:`visible_assistants` skips it.
    """
    if (resolved := resolve_ref(user, ref)) is None:
        return None

    return await asyncio.to_thread(_named, *resolved)


def apply_assistant(config: RunChoices, assistant: Assistant) -> RunChoices:
    """*config* narrowed by *assistant*: its tools withheld, its mode capped, its documents added.

    >>> spec = AssistantSpec(instructions="x", disabled_tools=["web_search"], max_mode="read")
    >>> narrowed = apply_assistant(RunChoices(), Assistant(GlobalScope(), "a", spec))
    >>> narrowed.mode, narrowed.disabled_tools
    ('read', ['web_search'])
    """
    spec = assistant.spec

    return config.model_copy(
        update={
            "mode": min(config.mode, spec.max_mode, key=MODE_ORDER.index),
            "disabled_tools": sorted({*config.disabled_tools, *spec.disabled_tools}),
            "included_documents": [
                *config.included_documents,
                *spec.included_documents,
            ],
            "excluded_documents": [
                *config.excluded_documents,
                *spec.excluded_documents,
            ],
        }
    )


async def save_assistant(
    user: User, prefix: str, name: str, spec: AssistantSpec
) -> Assistant:
    """Save *spec* as the assistant *name* of the scope at *prefix*, replacing one of its name.

    Raises:
        HTTPException: For a scope the user may not change.
    """
    scope = writable_library(user, prefix).scope
    await asyncio.to_thread(
        atomic_write,
        assistant_library(scope) / f"{name}{_SUFFIX}",
        spec.model_dump_json(indent=2),
    )
    announce_library_changed("assistants", scope)

    return Assistant(scope, name, spec)


async def delete_assistant(user: User, prefix: str, name: str) -> None:
    """Delete the assistant *name* of the scope at *prefix*.

    Raises:
        HTTPException: For a scope the user may not change, or no such assistant.
    """
    scope = writable_library(user, prefix).scope
    path = assistant_library(scope) / f"{name}{_SUFFIX}"

    try:
        await asyncio.to_thread(path.unlink)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_not_found(name).current) from exc

    announce_library_changed("assistants", scope)


async def remove_assistants_root() -> None:
    """Remove every user's and group's assistants, for a factory reset."""
    await asyncio.to_thread(remove_path, _root())
