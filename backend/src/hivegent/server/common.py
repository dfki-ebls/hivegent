"""Shared helpers for server routes and operations."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Header, HTTPException
from pydantic import ValidationError

from ..auth import User
from ..config import (
    normalize_unicode,
    sanitize_document_path,
    sanitize_group_id,
    settings,
)
from ..llm_config import LlmConfig, LlmTier, resolve_llm_config
from ..security import require_safe_external_url
from ..store import Casebase, WorkspaceScope
from ..types import DocumentFilter
from .models import PipelineSpec

__all__ = [
    "ClientId",
    "group_store",
    "group_stores",
    "parse_document_scope",
    "parse_pipeline_spec",
    "prepare_llm_config",
    "require_group_member",
    "require_group_write",
    "resolve_move",
    "resolve_workspace_path",
    "safe_group_id",
    "safe_path",
    "user_store",
]


# Identifies the browser tab making a request, so a change it caused is not
# echoed back to it over the job feed — it re-reads the result itself. Absent
# for any client that does not name itself (the CLI, an MCP client), which then
# simply receives every notification.
type ClientId = Annotated[str | None, Header(alias="X-Client-Id")]


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


def user_store(user: User) -> Casebase:
    """Build the personal casebase for a user."""
    return Casebase.for_user(user.id)


def group_store(group_id: str) -> Casebase:
    """Build the casebase for a group."""
    return Casebase.for_group(group_id)


def group_stores(user: User, *, writable: bool = False) -> tuple[Casebase, ...]:
    """Build group casebases for the groups the user belongs to.

    With *writable* only the groups the user may write to are returned, the
    same permission :func:`resolve_workspace_path` enforces on the HTTP write
    routes, so the agent's mutating tools reach exactly the workspaces the API
    would let the user mutate by hand.
    """
    groups = user.write_groups if writable else user.all_groups
    return tuple(group_store(group_id) for group_id in groups)


def parse_pipeline_spec(raw: str) -> PipelineSpec:
    """Parse a JSON-encoded ``PipelineSpec`` form field."""
    try:
        return PipelineSpec.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid pipeline_spec: {exc}",
        ) from exc


def safe_path(filepath: str) -> str:
    """Sanitize a document filepath from a URL path parameter."""
    try:
        return sanitize_document_path(filepath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def safe_group_id(group_id: str) -> str:
    """Sanitize a group ID from a URL path parameter."""
    try:
        return sanitize_group_id(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def require_group_member(user: User, group_id: str) -> str:
    """Validate the group ID and require group membership."""
    safe_id = safe_group_id(group_id)
    if safe_id not in user.all_groups:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return safe_id


def require_group_write(user: User, group_id: str) -> str:
    """Validate the group ID and require group write access."""
    safe_id = safe_group_id(group_id)
    if safe_id not in user.write_groups:
        raise HTTPException(
            status_code=403,
            detail="Write access required for this group",
        )
    return safe_id


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
    group_id = scope.group_id
    if group_id is not None:
        safe_id = (
            require_group_write(user, group_id)
            if write
            else require_group_member(user, group_id)
        )
        store = group_store(safe_id)
    else:
        store = user_store(user)
    return store, safe_path(local) if local else ""


def resolve_move(
    user: User, source: str, destination: str
) -> tuple[Casebase, str, Casebase, str]:
    """Resolve both endpoints of a move, requiring write access to each.

    A move may stay within one workspace or migrate between two (personal ↔
    group, group ↔ group); either way it writes both ends, so both are resolved
    with ``write=True``. Centralizing the pair here keeps that invariant in one
    place instead of duplicating it across the single, bulk, and directory move
    endpoints.
    """
    src_store, src = resolve_workspace_path(user, source, write=True)
    dst_store, dst = resolve_workspace_path(user, destination, write=True)
    return src_store, src, dst_store, dst
