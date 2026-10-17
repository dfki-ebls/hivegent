"""Shared helpers for server routes and operations."""

import asyncio
from collections.abc import Iterator
from typing import Annotated

from fastapi import Header, HTTPException
from pydantic import ValidationError

from .. import workspace
from ..agents import RunPrefix, UserDeps, build_capabilities
from ..changes import WorkspaceChanged
from ..config import (
    normalize_unicode,
    sanitize_document_path,
    settings,
)
from ..conversation_files import delete_conversations
from ..db.conversations import OwnedConversation
from ..db.users import delete_user
from ..l10n import Localized, current_language
from ..llm import model_from_config
from ..llm_config import LlmConfig, LlmTier, resolve_llm_config
from ..mcp import build_mcp_server, validate_mcp_servers
from ..prompts import compose_instructions
from ..security import require_safe_external_url
from ..store import AccessDenied, Casebase, User, WorkspaceScope
from ..tmp import tmp_dir
from ..types import AgentRunConfig, DocumentFilter
from ..workspace_events import announce_workspace_changed
from .models import PipelineSpec

__all__ = [
    "ClientId",
    "build_run_prefix",
    "delete_user_data",
    "parse_document_scope",
    "parse_pipeline_spec",
    "prepare_llm_config",
    "resolve_workspace_path",
    "safe_path",
]


# Identifies the browser tab making a request, so a change it caused is not
# echoed back to it over the job feed — it re-reads the result itself. Absent
# for any client that does not name itself (the CLI, an MCP client), which then
# simply receives every notification.
type ClientId = Annotated[str | None, Header(alias="X-Client-Id")]


def _invalid_pipeline_spec(error: str) -> Localized[str]:
    return Localized(
        en=f"Invalid pipeline_spec: {error}", de=f"Ungültige pipeline_spec: {error}"
    )


def prepare_llm_config(llm: LlmConfig, *, tier: LlmTier = "aux") -> LlmConfig:
    """Resolve defaults and check user-provided ``base_url`` values.

    Centralizes the request-boundary check so each route can stay a
    one-liner.
    Pydantic checks URL shape, while this hook applies the user URL allowlist.
    Server-configured base URLs are trusted operator input.

    *tier* selects which configured ``(model, max_tokens)`` pair backs the
    request; it defaults to ``"aux"`` so the many ancillary routes (titles,
    image alt-text during upload) are right by default, while the chat and
    compaction routes pass ``tier="main"``.
    """
    if llm.base_url:
        try:
            require_safe_external_url(
                llm.base_url,
                "LLM base_url",
                policy=settings.security.user_policy(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return resolve_llm_config(llm, tier=tier)


def parse_document_scope(
    included_documents: list[str],
    excluded_documents: list[str],
    user_groups: frozenset[str],
) -> tuple[frozenset[str], DocumentFilter | None, dict[str, DocumentFilter]]:
    """Parse the request's document selection into its two halves.

    Entries are canonical workspace paths; a bare scope root selects the
    whole workspace (local ``/``). Unparseable entries and groups the caller
    cannot address (``user_groups``, i.e. :attr:`User.all_groups`) are
    skipped from both halves.  Every entry is folded to NFC, so a selection
    captured from a workspace tree before the paths were canonicalized still
    names its document instead of silently matching nothing.

    The halves are not symmetric.  The included list restricts nothing: it is
    re-rendered as canonical paths for the prompt to name, so a conversation
    pointed at two files still reaches the whole workspace.  Only the excluded
    list becomes a :class:`DocumentFilter`, and only for the stores it names —
    every other store stays unfiltered.

    Returns:
        The canonical paths to name to the model, the personal store's filter
        (``None`` when it hides nothing), and one filter per named group.
    """

    def scoped(entries: list[str]) -> Iterator[tuple[WorkspaceScope, str]]:
        """Yield each addressable entry as its scope and its local path."""
        for entry in entries:
            try:
                scope, local = WorkspaceScope.parse(normalize_unicode(entry))
            except ValueError:
                continue

            if scope.group_id is None or scope.group_id in user_groups:
                yield scope, local or "/"

    relevant = frozenset(
        scope.render_filter_entry(local) for scope, local in scoped(included_documents)
    )

    hidden: dict[str | None, set[str]] = {}
    for scope, local in scoped(excluded_documents):
        hidden.setdefault(scope.group_id, set()).add(local)

    filters = {
        store_id: DocumentFilter(excluded=frozenset(locals_))
        for store_id, locals_ in hidden.items()
    }

    return (
        relevant,
        filters.pop(None, None),
        {group_id: f for group_id, f in filters.items() if group_id is not None},
    )


def build_run_prefix(
    config: AgentRunConfig, conversation: OwnedConversation
) -> RunPrefix:
    """Compose the prompt prefix *config* describes, for chat or compaction.

    One builder for both callers on purpose: a compaction request asks for its
    summary as one more turn of the conversation, and that only pays off while
    its instructions, tool schemas, and document scope match the chat turn
    before it token for token.

    It also owns the preconditions of that match, so no caller can compose a
    prefix that differs by forgetting one: the LLM config is resolved (and
    returned on the result, since prefix and model go together), the MCP list
    is validated before its toolsets are built, and the request's interface
    language is read once and handed to every part of the prefix explicitly.
    """
    language = current_language()
    user = conversation.user

    try:
        validate_mcp_servers(config.tools.mcp_servers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The conversation's `/tmp` exists for as long as it runs, as in a
    # container session.
    tmp_dir(settings.data_dir, conversation).mkdir(parents=True, exist_ok=True)

    llm = prepare_llm_config(config.llm, tier="main")
    relevant_documents, document_filter, group_filters = parse_document_scope(
        config.included_documents,
        config.excluded_documents,
        user.all_groups,
    )

    deps = UserDeps(
        user=user,
        mode=config.mode,
        conversation=conversation,
        document_filter=document_filter,
        group_filters=group_filters,
        relevant_documents=relevant_documents,
        disabled_tools=frozenset(config.tools.disabled_tools),
        llm=llm,
        aux_llm=prepare_llm_config(config.llm),
        language=language,
    )

    return RunPrefix(
        deps=deps,
        capabilities=build_capabilities(
            config.tools,
            extra=[build_mcp_server(server) for server in config.tools.mcp_servers],
            mode=deps.mode,
            language=language,
        ),
        instructions=compose_instructions(
            config.personality, config.system_message, language
        ),
        llm=llm,
        model=model_from_config(llm),
    )


def parse_pipeline_spec(raw: str) -> PipelineSpec:
    """Parse a JSON-encoded ``PipelineSpec`` form field."""
    try:
        return PipelineSpec.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=_invalid_pipeline_spec(str(exc)).current,
        ) from exc


def safe_path(filepath: str) -> str:
    """Sanitize a document filepath from a URL path parameter."""
    try:
        return sanitize_document_path(filepath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def resolve_workspace_path(
    user: User, path: str, *, write: bool = False
) -> tuple[Casebase, str]:
    """Resolve a canonical workspace path to its store and local path.

    Personal documents carry a ``~`` prefix, group documents an
    ``@<group>`` prefix — the convention owned by :class:`WorkspaceScope`. For
    group paths this enforces membership, or write access when *write* is true,
    so a caller cannot reach a group they do not belong to by crafting a prefix.

    A bare scope root (``~`` or ``@<group>``) resolves to the store with an
    empty local path. A bare, unprefixed path raises ``400``.
    """
    try:
        scope, local = WorkspaceScope.parse(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        store = (
            user.store
            if scope.group_id is None
            else user.group_store(scope.group_id, writable=write)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return store, safe_path(local) if local else ""


async def delete_user_data(user: User, *, exclude_client: str | None = None) -> None:
    """Delete everything *user* owns and announce their emptied workspace.

    The workspace (documents, cascading to chunks, and the directory) and the
    conversations (rows and folders) go independently, and deleting the
    ``User`` row then cascades to memory.  The user re-materialises lazily on
    the next request.
    """
    store = user.store
    await asyncio.gather(workspace.delete_all(store), delete_conversations(user))
    await delete_user(user.id)
    announce_workspace_changed(
        user.id, WorkspaceChanged((store.scope.prefix,)), exclude_client=exclude_client
    )
