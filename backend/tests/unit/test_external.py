"""The operator's SQL databases and HTTP APIs as agent tools: effects, visibility, and boot validation."""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from hivegent.agents.external import DataSource, _api, _database
from hivegent.config import Settings
from hivegent.config.integrations import ApiSettings, SqlDatabaseSettings
from hivegent.shared.l10n import Language
from hivegent.store import User

SPEC = {
    "paths": {
        "/pets/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True}],
            "get": {"operationId": "getPet"},
            "delete": {"operationId": "deletePet"},
        }
    }
}


def _effects(source: DataSource) -> dict[str, str]:
    return {
        name: tool.metadata["effect"]
        for name, tool in source.toolset.tools.items()
        if tool.metadata
    }


def test_database_offers_reading_tools_and_its_statements_to_its_groups(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'erp.db'}"

    with sa.create_engine(url).begin() as conn:
        _ = conn.exec_driver_sql("create table orders (id integer)")

    config = SqlDatabaseSettings.model_validate(
        {
            "url": url,
            "groups": ["sales"],
            "tables": ["orders"],
            "statements": {
                "close": {
                    "description": "Close an order.",
                    "sql": "delete from orders where id = :id",
                    "params": {"id": "integer"},
                    "effect": "write",
                }
            },
        }
    )
    source = _database("erp", config)

    assert _effects(source) == {
        "erp_tables": "read",
        "erp_describe": "read",
        "erp_select": "read",
        "erp_close": "write",
    }
    assert User("u", read_groups=frozenset({"sales"})).sees(source.groups)
    assert not User("v").sees(source.groups)


def test_database_instructions_follow_the_language_of_the_run() -> None:
    source = _database(
        "erp",
        SqlDatabaseSettings(
            url="sqlite://", tables=["orders"], instructions="Orders only."
        ),
    )

    leads: dict[Language, str] = {"en": "Database erp:", "de": "Datenbank erp:"}

    for language, lead in leads.items():
        text = source.instructions[language]

        assert text.startswith(lead) and text.endswith("Orders only.")


def test_api_offers_its_operations_with_effects(tmp_path: Path) -> None:
    spec = tmp_path / "crm.json"
    _ = spec.write_text(json.dumps(SPEC))
    config = ApiSettings.model_validate(
        {
            "url": "https://crm.test/v1",
            "spec": spec,
            "operations": ["getPet", "deletePet"],
        }
    )
    source = _api("crm", config)

    assert _effects(source) == {"crm_getPet": "read", "crm_deletePet": "write"}
    assert source.integration().model_dump() == {
        "kind": "api",
        "id": "crm",
        "host": "crm.test",
        "groups": [],
        "tools": {"crm_getPet": "read", "crm_deletePet": "write"},
    }
    assert User("bot", kind="service").sees(source.groups)

    with pytest.raises(ValueError, match="nope"):
        _ = _api("crm", config.model_copy(update={"operations": ["nope"]}))


def test_api_headers_merge_plain_and_secret_values_but_refuse_a_clash(
    tmp_path: Path,
) -> None:
    base = {
        "url": "https://cloud.test/ocs/v2.php",
        "spec": tmp_path,
        "operations": ["x"],
    }
    config = ApiSettings.model_validate(
        base
        | {
            "headers": {"OCS-APIRequest": "true"},
            "secret_headers": {"Authorization": "Basic s3cret"},
        }
    )

    assert config.request_headers == {
        "OCS-APIRequest": "true",
        "Authorization": "Basic s3cret",
    }

    with pytest.raises(ValidationError, match="authorization"):
        _ = ApiSettings.model_validate(
            base
            | {
                "headers": {"authorization": "plain"},
                "secret_headers": {"Authorization": "secret"},
            }
        )


def test_settings_refuse_a_password_in_the_url_and_a_reused_id() -> None:
    with pytest.raises(ValidationError, match="credential"):
        _ = SqlDatabaseSettings(url="postgresql://u:secret@h/db", tables=["t"])

    with pytest.raises(ValidationError, match="tables or statements"):
        _ = SqlDatabaseSettings(url="sqlite://")

    databases = {"x": {"url": "sqlite://", "tables": ["t"]}}

    with pytest.raises(ValidationError, match="twice"):
        _ = Settings.model_validate(
            {
                "databases": databases,
                "mcp_servers": {"x": {"url": "https://x.test/mcp"}},
            }
        )
