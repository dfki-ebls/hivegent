"""deferred tool requests

A run that pauses now stores its ``DeferredToolRequests`` on its last response
under ``deferredToolRequests``, which the next request resumes against, so a
turn still awaiting an approval or an answer gets them from its unanswered
calls and its ``approvalMetadata``, which stays for the client.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-04 12:00:00.000000+00:00
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFERRED = "deferredToolRequests"
_APPROVAL_METADATA = "approvalMetadata"
_ASK_USER = "ask_user"

# A paused run ends on its response, or on the request answering the calls it
# ran beside the deferred ones, so only a leaf and its parent can hold one.
_LEAVES = """
SELECT l.conversation_id, l.id, l.payload, p.id AS parent_id, p.payload AS parent
FROM messages l
LEFT JOIN messages p ON p.conversation_id = l.conversation_id AND p.id = l.parent_id
WHERE NOT EXISTS (
    SELECT 1 FROM messages c
    WHERE c.conversation_id = l.conversation_id AND c.parent_id = l.id
)
"""

_SET_PAYLOAD = sa.text(
    "UPDATE messages SET payload = CAST(:payload AS jsonb) "
    "WHERE conversation_id = :conversation_id AND id = :id"
)

_DROP_DEFERRED = f"""
UPDATE messages SET payload = payload #- '{{metadata,{_DEFERRED}}}'
WHERE payload->'metadata' ? '{_DEFERRED}'
"""


def paused(
    leaf_id: str,
    leaf: Mapping[str, Any],
    parent_id: str | None,
    parent: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]] | None:
    """The response the branch ending on *leaf* paused on, its requests stored.

    Returns the id of that response and its new payload, or ``None`` when the
    branch awaits nothing.
    """
    if leaf["kind"] == "response":
        response_id, response, answered = leaf_id, leaf, set[str]()

    elif (
        parent_id is not None
        and parent is not None
        and parent["kind"] == "response"
        and leaf["parts"]
        and all(part["part_kind"] == "tool-return" for part in leaf["parts"])
    ):
        response_id, response = parent_id, parent
        answered = {part["tool_call_id"] for part in leaf["parts"]}

    else:
        return None

    pending = [
        part
        for part in response["parts"]
        if part["part_kind"] == "tool-call" and part["tool_call_id"] not in answered
    ]

    if not pending:
        return None

    metadata = dict(response.get("metadata") or {})
    approval_metadata = metadata.get(_APPROVAL_METADATA) or {}
    approvals = [part for part in pending if part["tool_name"] != _ASK_USER]
    metadata[_DEFERRED] = {
        "calls": [part for part in pending if part["tool_name"] == _ASK_USER],
        "approvals": approvals,
        "metadata": {
            part["tool_call_id"]: approval_metadata[part["tool_call_id"]]
            for part in approvals
            if part["tool_call_id"] in approval_metadata
        },
    }

    return response_id, {**response, "metadata": metadata}


def upgrade() -> None:
    bind = op.get_bind()

    for row in bind.execute(sa.text(_LEAVES)).mappings().all():
        if (
            found := paused(row["id"], row["payload"], row["parent_id"], row["parent"])
        ) is None:
            continue

        response_id, payload = found
        bind.execute(
            _SET_PAYLOAD,
            {
                "payload": json.dumps(payload),
                "conversation_id": row["conversation_id"],
                "id": response_id,
            },
        )


def downgrade() -> None:
    op.execute(_DROP_DEFERRED)
