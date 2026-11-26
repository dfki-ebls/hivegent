"""The contract the frontend mirrors, pinned so that its drift fails a test.

The fixture holds the JSON Schemas of the REST API and of the data that
reaches the client outside of it (data parts, tool metadata, tool arguments),
plus every tool name a chat may show, a program's functions and the
delegation tools included.  ``frontend/src/test/api/contract.test.ts``
checks the zod schemas and the tool labels and icons against it.  The fixture
is regenerated with ``UPDATE_FIXTURES=1``.
"""

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import TypeAdapter

from hivegent.agents.capabilities import SANDBOX_FUNCTION_NAMES, TOOL_NAMES
from hivegent.agents.subagents import DELEGATION_TOOLS
from hivegent.server.routes import api_router
from hivegent.server.routes.public import router as public_router
from hivegent.tools.changes import WriteMode
from hivegent.tools.changeset import ChangesetOutcome

_FIXTURE = Path(__file__).parents[3] / "frontend/src/test/fixtures/contract.json"

_REF = "#/components/schemas/{model}"

# Wire shapes outside the REST API, by the name the frontend test looks them up by.
_EXTRA = {
    "ChangesetOutcome": TypeAdapter(ChangesetOutcome),
    "WriteMode": TypeAdapter(WriteMode),
}


def _schemas() -> dict[str, Any]:
    app = FastAPI()
    app.include_router(public_router)
    app.include_router(api_router)
    schemas: dict[str, Any] = dict(app.openapi()["components"]["schemas"])

    for name, adapter in _EXTRA.items():
        schema = adapter.json_schema(ref_template=_REF)
        schemas.update(schema.pop("$defs", {}))
        schemas[name] = schema

    return dict(sorted(schemas.items()))


def test_the_frontend_contract_is_current() -> None:
    contract = {
        "schemas": _schemas(),
        "tools": sorted(TOOL_NAMES | SANDBOX_FUNCTION_NAMES | DELEGATION_TOOLS.keys()),
    }
    text = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"

    if os.environ.get("UPDATE_FIXTURES"):
        _FIXTURE.write_text(text)

    assert _FIXTURE.read_text() == text, (
        f"{_FIXTURE.name} is stale, set UPDATE_FIXTURES=1"
    )
