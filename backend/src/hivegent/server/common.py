"""Shared helpers for server routes and operations."""

from fastapi import HTTPException
from pydantic import ValidationError

from ..auth import User
from ..config import sanitize_document_path, sanitize_group_id
from ..store import Casebase
from ..types import DocumentFilter, resolve_llm_config
from .models import PipelineSpec

__all__ = [
    "group_store",
    "group_stores",
    "parse_document_filters",
    "parse_pipeline_spec",
    "require_group_member",
    "require_group_write",
    "resolve_llm_config",
    "safe_group_id",
    "safe_path",
    "user_store",
]


def parse_document_filters(
    included_documents: list[str],
    excluded_documents: list[str],
    user_groups: frozenset[str],
) -> tuple[DocumentFilter | None, dict[str, DocumentFilter]]:
    """Parse include and exclude lists into per-store document filters."""
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
    return Casebase(kind="user", id=user.id)


def group_store(group_id: str) -> Casebase:
    """Build the casebase for a group."""
    return Casebase(kind="group", id=group_id)


def group_stores(user: User) -> tuple[Casebase, ...]:
    """Build group casebases from the user's memberships."""
    stores: list[Casebase] = []
    for group_id in user.all_groups:
        try:
            stores.append(group_store(group_id))
        except ValueError:
            continue
    return tuple(stores)


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
    """Validate the group ID and require membership."""
    safe_id = safe_group_id(group_id)
    if safe_id not in user.all_groups:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return safe_id


def require_group_write(user: User, group_id: str) -> str:
    """Validate the group ID and require write access."""
    safe_id = safe_group_id(group_id)
    if safe_id not in user.write_groups:
        raise HTTPException(
            status_code=403,
            detail="Write access required for this group",
        )
    return safe_id
