"""Shared helpers for server routes and operations."""

from fastapi import HTTPException
from pydantic import ValidationError

from ..auth import User
from ..config import sanitize_document_path, sanitize_group_id, settings
from ..security import validate_optional_external_url
from ..store import Casebase
from ..types import DocumentFilter, LlmConfig, resolve_llm_config
from .models import PipelineSpec

__all__ = [
    "group_store",
    "group_stores",
    "parse_document_filters",
    "parse_pipeline_spec",
    "prepare_llm_config",
    "require_group_member",
    "require_group_write",
    "resolve_workspace_path",
    "safe_group_id",
    "safe_path",
    "user_store",
]


async def prepare_llm_config(
    llm: LlmConfig, *, default_model: str | None = None
) -> LlmConfig:
    """Resolve defaults and run the SSRF check on ``base_url``.

    Centralizes the request-boundary check so each route can stay a
    one-liner. Pydantic only checks URL shape; this is the async hook
    that actually resolves the host and rejects private targets.

    *default_model* defaults to :attr:`settings.llm.aux_model` so the many
    ancillary routes (titles, compaction, image alt-text during upload)
    are right by default; the chat route passes the main model explicitly.
    """
    if default_model is None:
        default_model = settings.llm.aux_model
    resolved = resolve_llm_config(llm, default_model=default_model)
    try:
        await validate_optional_external_url(resolved.base_url, "LLM base_url")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return resolved


def parse_document_filters(
    included_documents: list[str],
    excluded_documents: list[str],
    user_groups: frozenset[str],
) -> tuple[DocumentFilter | None, dict[str, DocumentFilter]]:
    """Parse include and exclude lists into per-store document filters.

    ``user_groups`` is the set of groups whose documents the caller may
    address — pass :attr:`User.all_groups`.
    """
    user_included: list[str] = []
    user_excluded: list[str] = []
    group_included: dict[str, list[str]] = {}
    group_excluded: dict[str, list[str]] = {}

    for entry in included_documents:
        if entry.startswith("@") and "/" in entry:
            group_id, _, path = entry[1:].partition("/")
            if group_id in user_groups:
                group_included.setdefault(group_id, []).append(path or "/")
        else:
            user_included.append(entry)

    for entry in excluded_documents:
        if entry.startswith("@") and "/" in entry:
            group_id, _, path = entry[1:].partition("/")
            if group_id in user_groups:
                group_excluded.setdefault(group_id, []).append(path or "/")
        else:
            user_excluded.append(entry)

    user_filter: DocumentFilter | None = None
    if user_included or user_excluded:
        user_filter = DocumentFilter(
            included=frozenset(user_included),
            excluded=frozenset(user_excluded),
        )

    group_filters: dict[str, DocumentFilter] = {}
    for group_id in set(group_included) | set(group_excluded):
        group_filters[group_id] = DocumentFilter(
            included=frozenset(group_included.get(group_id, [])),
            excluded=frozenset(group_excluded.get(group_id, [])),
        )

    return user_filter, group_filters


def user_store(user: User) -> Casebase:
    """Build the personal casebase for a user."""
    return Casebase.for_user(user.id)


def group_store(group_id: str) -> Casebase:
    """Build the casebase for a group."""
    return Casebase.for_group(group_id)


def group_stores(user: User) -> tuple[Casebase, ...]:
    """Build group casebases for every group the user belongs to."""
    return tuple(group_store(group_id) for group_id in user.all_groups)


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

    Group documents carry an ``@<group>/<local>`` prefix; personal
    documents have none — the same convention used by
    :func:`parse_document_filters` and the tools-layer ``SearchPath.prefix``.
    For group paths this enforces membership, or write access when *write*
    is true, so a caller cannot reach a group they do not belong to by
    crafting a prefix.

    A bare scope (``""`` for personal, ``"@<group>/"`` for a group root)
    resolves to the store with an empty local path.
    """
    if path.startswith("@") and "/" in path:
        group_id, _, local = path[1:].partition("/")
        safe_id = (
            require_group_write(user, group_id)
            if write
            else require_group_member(user, group_id)
        )
        return group_store(safe_id), safe_path(local) if local else ""
    return user_store(user), safe_path(path) if path else ""
