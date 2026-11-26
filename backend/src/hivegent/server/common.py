"""Shared helpers for server routes and operations."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import replace
from uuid import UUID

from fastapi import HTTPException

from .. import workspace
from ..agents import RunPrefix, UserDeps, build_capabilities
from ..agents.integrations import integration_capabilities
from ..agents.subagents import delegation
from ..config import parse_uuid, sanitize_document_path, settings
from ..conversation_files import delete_conversations
from ..db.conversations import OwnedConversation
from ..db.users import delete_user, load_preferences
from ..files import remove_path
from ..jobs import manager
from ..libraries import LibraryGrant
from ..llm import model_from_config
from ..llm_config import LlmConfig, ReasoningEffort, chat_llm
from ..prompts import compose_instructions
from ..shared.l10n import Localized, current_language
from ..shared.text import normalize_unicode
from ..skills import library
from ..store import (
    AccessDenied,
    User,
    Workspace,
    WorkspaceScope,
    split_scope_path,
)
from ..types import (
    DocumentFilter,
    DocumentSelection,
    FilterEntry,
    Preferences,
)

__all__ = [
    "CONVERSATION_NOT_FOUND",
    "build_run_prefix",
    "delete_scope_data",
    "delete_user_data",
    "found",
    "parse_document_scope",
    "require_chat_llm",
    "resolve_workspace",
    "resolve_workspace_path",
    "safe_path",
    "user_preferences",
]


CONVERSATION_NOT_FOUND = Localized(
    en="Conversation not found", de="Konversation nicht gefunden"
)


def found[T](value: T | None) -> T:
    """*value*, or ``404`` for a conversation that is missing or not the caller's."""
    if value is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND.current)

    return value


async def found_by_id[T](
    conversation_id: str, load: Callable[[UUID], Awaitable[T | None]]
) -> T:
    """What *load* finds for a path's id, ``404`` for an id that is no UUID or finds none."""
    parsed = parse_uuid(conversation_id)

    return found(None if parsed is None else await load(parsed))


_NO_CHAT_MODEL = Localized(
    en="No chat model is configured", de="Es ist kein Chat-Modell konfiguriert"
)


def require_chat_llm(model_id: str | None, thinking: ReasoningEffort) -> LlmConfig:
    """The chat model *model_id* reasoning with *thinking*, the default model for ``None``, ``503`` without a catalog."""
    llm = chat_llm(model_id, thinking)
    if llm is None:
        raise HTTPException(status_code=503, detail=_NO_CHAT_MODEL.current)

    return llm


def _invalid_scope(scope: str) -> Localized[str]:
    return Localized(
        en=f"Not a workspace: {scope!r}", de=f"Kein Arbeitsbereich: „{scope}“"
    )


def parse_document_scope(
    included_documents: Sequence[FilterEntry],
    excluded_documents: Sequence[FilterEntry],
    user: User,
) -> tuple[frozenset[str], dict[WorkspaceScope, DocumentFilter]]:
    """Parse the request's document selection into its two halves.

    Entries name canonical workspace paths, a bare scope root as a directory
    selecting the whole workspace (the empty local path).  Unparseable entries
    and scopes *user* does not see are skipped from both halves.  Every entry
    is folded to NFC,
    so a selection captured from a workspace tree before the paths were
    canonicalized still names its document instead of silently matching
    nothing.

    The halves are not symmetric.  The included list restricts nothing: it is
    re-rendered as canonical paths for the prompt to name, so a conversation
    pointed at two files still reaches the whole workspace.  Only the excluded
    list becomes a :class:`DocumentFilter`, and only for the scopes it names,
    every other scope stays unfiltered.

    Returns:
        The canonical paths to name to the model, and one filter per scope
        the excluded list names.
    """
    visible = {store.scope.prefix: store.scope for store in user.workspaces()}

    def scoped(
        entries: Sequence[FilterEntry],
    ) -> Iterator[tuple[WorkspaceScope, FilterEntry]]:
        """Yield each addressable entry as its scope and its entry by local path."""
        for entry in entries:
            try:
                prefix, local = split_scope_path(normalize_unicode(entry.path))
            except ValueError:
                continue

            if (scope := visible.get(prefix)) is not None:
                yield scope, replace(entry, path=local)

    relevant = frozenset(
        scope.render_filter_entry(local.path, directory=local.directory)
        for scope, local in scoped(included_documents)
    )

    hidden: dict[WorkspaceScope, set[FilterEntry]] = {}

    for scope, local in scoped(excluded_documents):
        hidden.setdefault(scope, set()).add(local)

    return relevant, {
        scope: DocumentFilter(excluded=frozenset(locals_))
        for scope, locals_ in hidden.items()
    }


async def user_preferences(user: User) -> Preferences:
    """The preferences *user* stored, as runs apply them."""
    return Preferences.stored(await load_preferences(user.id))


async def build_run_prefix(
    selection: DocumentSelection,
    preferences: Preferences,
    user: User,
    conversation: OwnedConversation,
) -> RunPrefix:
    """Compose the prompt prefix of *user*'s *preferences* and the documents of *selection*, for a turn or a compaction.

    A compaction asks for its summary as one more turn of the conversation,
    which only pays off while its instructions and tool schemas match the turn
    before it token for token, so both run under the prefix built here.

    It also owns the preconditions of that match, so no caller can compose a
    prefix that differs by forgetting one: the LLM config is resolved (and
    returned on the result, since prefix and model go together), the
    request's interface language is read once and handed to every part of the
    prefix explicitly, and the subagents the turn delegates to join it
    (:func:`~hivegent.agents.subagents.delegation`), since their tools and
    listing are part of it.
    """
    language = current_language()
    llm = require_chat_llm(preferences.model, preferences.reasoning_effort)
    relevant_documents, document_filters = parse_document_scope(
        selection.included, selection.excluded, user
    )

    deps = await UserDeps(
        user=user,
        # A service answers many people at once, so it only ever reads.
        mode="read" if user.is_service else preferences.mode,
        conversation=conversation,
        document_filters=document_filters,
        relevant_documents=relevant_documents,
        instructions=preferences.instructions,
        llm=llm,
        language=language,
    ).with_roots()

    prefix = RunPrefix(
        deps=deps,
        capabilities=build_capabilities(
            deps, extra=integration_capabilities(user, language)
        ),
        instructions=compose_instructions(language, settings.llm.instructions),
        llm=llm,
        model=model_from_config(llm),
    )

    return replace(prefix, capabilities=[*prefix.capabilities, *delegation(prefix)])


def safe_path(filepath: str) -> str:
    """Sanitize a document filepath from a URL path parameter."""
    try:
        return sanitize_document_path(filepath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def resolve_workspace_path(
    user: User, path: str, *, write: bool = False
) -> tuple[Workspace, str]:
    """Resolve a canonical workspace path to its store and local path.

    The scope prefix (``~``, ``@<group>``, or the global one) names the
    workspace, which *user* must see, and write when *write* is true, so a
    caller cannot reach a workspace beyond its grant by crafting a prefix.

    A bare scope root resolves to the store with an empty local path. A bare,
    unprefixed path raises ``400``, a workspace beyond the grant ``403``.
    """
    try:
        prefix, local = split_scope_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        store = user.workspace(prefix, writable=write)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return store, safe_path(local) if local else ""


def resolve_workspace(user: User, scope: str, *, write: bool = False) -> Workspace:
    """Resolve a bare scope root (``~``, ``@<group>``, the global one) to its store.

    Raises ``400`` for a path below the root, which names no whole workspace.
    """
    store, local = resolve_workspace_path(user, scope, write=write)

    if local:
        raise HTTPException(status_code=400, detail=_invalid_scope(scope).current)

    return store


async def delete_scope_data(granted: LibraryGrant) -> None:
    """Delete the workspace and skills of *granted*'s scope, announcing the emptied workspace."""
    await asyncio.gather(
        workspace.delete_all(granted.store),
        asyncio.to_thread(remove_path, library(granted.scope).path),
    )


async def delete_user_data(user: User) -> None:
    """Delete everything *user* owns.

    Their jobs are cancelled first, so no queued upload lands afterwards.
    Their scope's data (:func:`delete_scope_data`) and the conversations
    (turns, rows, and folders) go independently, and deleting the ``User``
    row then cascades to memory.  The user re-materialises lazily on the
    next request.
    """
    await manager.cancel_owner(user.id)
    await asyncio.gather(
        delete_scope_data(LibraryGrant(user.home)),
        delete_conversations(user),
    )
    await delete_user(user.id)
