"""The external MCP server catalog: effects, visibility, and failure isolation."""

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.client.auth import ClientCredentialsOAuthProvider
from fastmcp.client.transports import StreamableHttpTransport
from mcp import MCPError
from mcp.shared.auth import OAuthMetadata
from mcp.types import CONNECTION_CLOSED, ToolAnnotations
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents import capabilities, catalog, integrations
from hivegent.agents.app import chat_agent
from hivegent.agents.capabilities import build_capabilities, unknown_tool_names
from hivegent.agents.catalog import CatalogServer
from hivegent.agents.common import UserDeps
from hivegent.agents.subagents import DELEGATION_TOOLS
from hivegent.config import settings
from hivegent.config.integrations import HttpMcpServer, McpServer, StdioMcpServer
from hivegent.security import OperatorUrl, UnsafeUrlError
from hivegent.store import User
from hivegent.types import Mode
from tests.helpers import in_run, run_context


def _config(config: dict[str, Any]) -> McpServer:
    return TypeAdapter(McpServer).validate_python(config)


def _app() -> FastMCP:
    app = FastMCP("crm", instructions="Look a record up before changing it.")

    @app.tool(annotations=ToolAnnotations(read_only_hint=True))
    def lookup(key: str) -> str:
        """Look a record up."""
        return f"record {key}"

    @app.tool
    def delete(key: str) -> str:
        """Delete a record."""
        return f"deleted {key}"

    @app.tool(annotations=ToolAnnotations(read_only_hint=True))
    def export() -> str:
        """Export every record."""
        return "everything"

    return app


def _server(**config: object) -> CatalogServer:
    toolset: MCPToolset[UserDeps] = MCPToolset(_app(), include_instructions=True)
    server = _config(
        {"url": "https://crm.test/mcp", "blocked_tools": ["export"], **config}
    )

    return CatalogServer("crm", server, catalog._client("crm", server, toolset))


@pytest.fixture
async def connected() -> AsyncIterator[CatalogServer]:
    server = _server(trusted=True)

    async with server.lifespan():
        while server.client.tools is None:
            await asyncio.sleep(0.01)

        yield server


async def _turn(
    server: CatalogServer, mode: Mode, *calls: ToolCallPart
) -> tuple[ModelRequestParameters, list[ModelMessage]]:
    """Run one turn offering *server*, making *calls* first, and what the model was handed."""
    captured: list[ModelRequestParameters] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(info.model_request_parameters)

        if len(messages) == 1 and calls:
            return ModelResponse(parts=list(calls))

        return ModelResponse(parts=[TextPart("ok")])

    deps = UserDeps(user=User("u"), mode=mode)
    result = await in_run(
        deps,
        chat_agent.run(
            "hi",
            model=FunctionModel(respond),
            deps=deps,
            capabilities=build_capabilities(deps, extra=[server.toolset]),
        ),
    )

    return captured[0], result.all_messages()


def _names(params: ModelRequestParameters) -> set[str]:
    return {tool.name for tool in params.function_tools if tool.name.startswith("crm_")}


async def test_a_trusted_servers_hints_and_the_mode_decide_what_is_offered(
    connected: CatalogServer,
) -> None:
    """Prefixed, blocked tools never, and read mode only what reads."""
    assert _names((await _turn(connected, "read"))[0]) == {"crm_lookup"}
    assert _names((await _turn(connected, "interactive"))[0]) == {
        "crm_lookup",
        "crm_delete",
    }


async def test_an_untrusted_servers_tools_all_execute_but_the_listed_reads() -> None:
    server = _server(effects={"lookup": "read"})
    server.client.offer(await server.client.toolset.list_tools())

    assert server.integration().tools == {
        "lookup": "read",
        "delete": "execute",
    }


async def test_an_unreachable_server_withholds_its_tools_and_fails_no_turn(
    connected: CatalogServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable(*_args: object) -> None:
        # How pydantic-ai reports a connection the server closed.
        raise ModelRetry("Connection closed") from MCPError(
            CONNECTION_CLOSED, "Connection closed"
        )

    monkeypatch.setattr(connected.client.toolset, "call_tool", unreachable)
    _, messages = await _turn(
        connected, "read", ToolCallPart("crm_lookup", {"key": "a"}, "call")
    )
    returned = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert "could not be reached" in returned[0].model_response_str()
    assert connected.client.tools is None

    params, _ = await _turn(connected, "read")
    assert not _names(params)
    assert "crm are unavailable" in "\n".join(
        part.content for part in params.instruction_parts or []
    )


def test_a_server_is_seen_by_its_groups_and_a_global_one_by_everyone() -> None:
    sales = _server(groups=["sales"])
    member = User("u", read_groups=frozenset({"sales"}))
    bridge = User("bot", kind="service")

    assert member.sees(sales.groups)
    assert not User("v").sees(sales.groups)
    assert not bridge.sees(sales.groups)
    assert bridge.sees(_server().groups)


def test_server_ids_name_their_tools_and_never_a_built_in_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config({"url": "https://crm.test/mcp"})
    monkeypatch.setattr(
        integrations, "INTEGRATIONS", (CatalogServer.build("crm", config),)
    )

    assert unknown_tool_names(["crm_anything", "web_x"]) == ["web_x"]

    monkeypatch.setattr(
        integrations, "INTEGRATIONS", (CatalogServer.build("read", config),)
    )
    with pytest.raises(ValueError, match="read_assistant.*read_tool_result"):
        capabilities.check_tool_settings()

    # Reserved even while subagents are disabled.
    monkeypatch.setattr(
        capabilities,
        "KNOWN_TOOL_NAMES",
        capabilities.KNOWN_TOOL_NAMES - DELEGATION_TOOLS.keys(),
    )
    monkeypatch.setattr(
        integrations, "INTEGRATIONS", (CatalogServer.build("cancel", config),)
    )
    with pytest.raises(ValueError, match="cancel_task"):
        capabilities.check_tool_settings()


async def test_a_server_told_who_calls_is_named_the_caller_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.auth, "service_client_id", "teams-bridge")
    config = _config(
        {"url": "https://crm.test/mcp", "headers": {"x-key": "k"}, "forward_user": True}
    )
    server = CatalogServer.build("crm", config)
    assert server.client.caller is not None
    assert (
        CatalogServer.build(
            "crm", config.model_copy(update={"forward_user": False})
        ).client.caller
        is None
    )

    for user, caller in (
        (User("alice", email="a@x.test", read_groups=frozenset({"sales"})), "alice"),
        (User("svc", kind="service"), "teams-bridge"),
    ):
        context = run_context(UserDeps(user=user, mode="read"))
        connection = await server.client.caller.for_run(context)
        transport = connection.client.transport

        assert isinstance(transport, StreamableHttpTransport)
        assert isinstance(auth := transport.auth, catalog._ServerAuth)
        assert auth.headers == {"x-key": "k", "X-Hivegent-User": caller}


def test_only_streamable_http_is_spoken() -> None:
    oauth = {"client_id": "hivegent", "client_secret": "s", "issuer": "https://id.test"}
    config = _config(
        {"url": "https://crm.test/mcp", "headers": {"x-key": "k"}, "oauth": oauth}
    )

    assert CatalogServer.build("crm", config).client.tools is None

    with pytest.raises(ValueError, match="SSE"):
        _ = CatalogServer.build("crm", _config({"url": "https://crm.test/sse"}))


async def test_oauth_reaches_the_endpoints_its_issuer_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The issuer's own metadata, and no other, opens its token endpoint's origin."""
    opened: list[httpx2.AsyncClient] = []
    create = catalog.create_trusted_http_client

    def recording(*urls: OperatorUrl, **kwargs: Any) -> httpx2.AsyncClient:
        opened.append(client := create(*urls, **kwargs))
        return client

    monkeypatch.setattr(catalog, "create_trusted_http_client", recording)
    oauth = {"client_id": "hivegent", "client_secret": "s", "issuer": "https://id.test"}
    config = {"url": "https://crm.test/mcp", "oauth": oauth}
    server = CatalogServer.build("crm", _config(config))
    (check,) = opened[0].event_hooks["request"]
    token = httpx2.Request("POST", "https://tokens.test/token")

    def metadata(issuer: str) -> OAuthMetadata:
        return OAuthMetadata.model_validate(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": "https://tokens.test/token",
            }
        )

    auth = server.client.toolset._static_auth
    assert isinstance(auth, catalog._ServerAuth)
    provider = auth.oauth
    assert isinstance(provider, ClientCredentialsOAuthProvider)

    for issuer, allowed in (("https://other.test", False), ("https://id.test", True)):
        provider.context.oauth_metadata = metadata(issuer)

        if allowed:
            await check(token)
        else:
            with pytest.raises(UnsafeUrlError):
                await check(token)


_STDIO_SERVER = """
import os
from fastmcp import FastMCP

app = FastMCP("local")

@app.tool
def environment() -> list[str]:
    return sorted(os.environ)

app.run()
"""


def test_a_stdio_server_takes_the_fields_of_its_transport_alone() -> None:
    stdio = {"command": "uvx", "args": ["mcp-server-git"], "secret_env": {"TOKEN": "t"}}

    assert isinstance(_config(stdio), StdioMcpServer)
    assert isinstance(_config({**stdio, "type": "stdio"}), StdioMcpServer)
    assert isinstance(_config({"url": "https://x.test/mcp"}), HttpMcpServer)
    assert isinstance(
        _config({"url": "https://x.test/mcp", "type": "streamable-http"}), HttpMcpServer
    )

    for invalid in (
        {**stdio, "forward_user": True},
        {**stdio, "url": "https://crm.test/mcp"},
        {**stdio, "env": {"TOKEN": "x"}},
        {"url": "https://crm.test/mcp", "command": "uvx", "type": "http"},
        {"url": "https://crm.test/sse", "type": "sse"},
    ):
        with pytest.raises(ValidationError):
            _ = _config(invalid)


async def test_a_stdio_server_runs_with_its_own_environment_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HIVEGENT_SECRET", "s")
    script = tmp_path / "server.py"
    _ = script.write_text(_STDIO_SERVER)
    config = _config(
        {
            "command": sys.executable,
            "args": [str(script)],
            "env": {"PLAIN": "p"},
            "secret_env": {"TOKEN": "t"},
            "trusted": True,
        }
    )
    server = CatalogServer.build("local", config)

    async with server.lifespan():
        async with asyncio.timeout(30):
            while server.client.tools is None:
                await asyncio.sleep(0.05)

        assert server.integration().host == sys.executable
        assert set(server.client.tools) == {"environment"}

        result = await server.client.toolset.client.call_tool("environment", {})

    assert {"PATH", "PLAIN", "TOKEN"} <= set(result.data)
    assert "HIVEGENT_SECRET" not in result.data
