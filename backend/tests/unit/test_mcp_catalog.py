"""The external MCP server catalog: effects, visibility, and failure isolation."""

import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
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
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import Capability
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
from pydantic_ai.toolsets import ToolsetTool

from hivegent.agents import capabilities, catalog, integrations
from hivegent.agents.app import chat_agent
from hivegent.agents.capabilities import build_capabilities, unknown_tool_names
from hivegent.agents.catalog import CatalogServer, caller_id
from hivegent.agents.common import UserDeps
from hivegent.agents.subagents import DELEGATION_TOOLS
from hivegent.config.integrations import HttpMcpServer, McpServer, StdioMcpServer
from hivegent.http_client import create_trusted_http_client
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


class _Closed(MCPToolset[UserDeps]):
    """A toolset whose server closed the connection, as pydantic-ai reports it."""

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> None:
        raise ModelRetry("Connection closed") from MCPError(
            CONNECTION_CLOSED, "Connection closed"
        )


def _server(
    toolset_type: type[MCPToolset[UserDeps]] = MCPToolset, **config: object
) -> CatalogServer:
    toolset = toolset_type(_app(), include_instructions=True)
    server = _config(
        {"url": "https://crm.test/mcp", "blocked_tools": ["export"], **config}
    )

    return CatalogServer("crm", server, catalog._client("crm", server, toolset))


@asynccontextmanager
async def _connect(server: CatalogServer) -> AsyncGenerator[CatalogServer]:
    """The server entered and its tools listed, as its supervisor does, without the wait."""
    async with server.client.toolset:
        server.client.offer(await server.client.toolset.list_tools())

        yield server


@pytest.fixture
async def connected() -> AsyncIterator[CatalogServer]:
    async with _connect(_server(trusted=True)) as server:
        yield server


@pytest.fixture
async def closed() -> AsyncIterator[CatalogServer]:
    async with _connect(_server(_Closed, trusted=True)) as server:
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
            capabilities=build_capabilities(
                deps, extra=[Capability(toolsets=[server.toolset])]
            ),
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


async def test_an_unreachable_server_keeps_its_tools_and_fails_no_turn(
    closed: CatalogServer,
) -> None:
    _, messages = await _turn(
        closed, "read", ToolCallPart("crm_lookup", {"key": "a"}, "call")
    )
    returned = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert "could not be reached" in returned[0].model_response_str()
    assert not closed.client.connected
    assert not closed.integration().connected

    params, messages = await _turn(
        closed, "read", ToolCallPart("crm_lookup", {"key": "a"}, "call")
    )
    assert _names(params) == {"crm_lookup"}
    assert "crm are unavailable" in "\n".join(
        part.model_response_str()
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )


def test_a_server_is_seen_by_its_groups_and_a_global_one_by_everyone() -> None:
    sales = _server(groups=["sales"])
    member = User("u", read_groups=frozenset({"sales"}))
    bridge = User("bot", kind="service")

    assert member.sees(sales.groups)
    assert not User("v").sees(sales.groups)
    assert not bridge.sees(sales.groups)
    assert bridge.sees(_server().groups)


@pytest.fixture
def serving(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Serve one external server of an id in place of the deployment's integrations."""

    def serve(server_id: str) -> None:
        config = _config({"url": "https://crm.test/mcp"})
        monkeypatch.setattr(
            integrations, "INTEGRATIONS", (CatalogServer.build(server_id, config),)
        )

    return serve


def test_server_ids_name_their_tools_and_never_a_built_in_one(
    serving: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    serving("crm")
    assert unknown_tool_names(["crm_anything", "web_x"]) == ["web_x"]

    serving("read")
    with pytest.raises(ValueError, match="read_binary_document.*read_tool_result"):
        capabilities.check_tool_settings()

    # Reserved even while subagents are disabled.
    monkeypatch.setattr(
        capabilities,
        "KNOWN_TOOL_NAMES",
        capabilities.KNOWN_TOOL_NAMES - DELEGATION_TOOLS.keys(),
    )
    serving("cancel")
    with pytest.raises(ValueError, match="cancel_task"):
        capabilities.check_tool_settings()


def test_a_service_is_named_by_its_client_to_a_server_told_who_calls() -> None:
    assert caller_id(User("svc", kind="service"), "teams-bridge") == "teams-bridge"
    assert caller_id(User("svc", kind="service"), None) == "svc"
    assert caller_id(User("alice"), "teams-bridge") == "alice"


async def test_a_server_told_who_calls_is_named_the_caller_alone() -> None:
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

    alice = User("alice", email="a@x.test", read_groups=frozenset({"sales"}))
    connection = await server.client.caller.for_run(
        run_context(UserDeps(user=alice, mode="read"))
    )
    transport = connection.client.transport

    assert isinstance(transport, StreamableHttpTransport)
    assert isinstance(auth := transport.auth, catalog._ServerAuth)
    assert auth.headers == {"x-key": "k", "X-Hivegent-User": "alice"}


async def test_oauth_reaches_the_endpoints_its_issuer_names() -> None:
    """The issuer's own metadata, and no other, opens its token endpoint's origin."""
    opened: list[httpx2.AsyncClient] = []

    def recording(*urls: OperatorUrl, **kwargs: Any) -> httpx2.AsyncClient:
        opened.append(client := create_trusted_http_client(*urls, **kwargs))
        return client

    oauth = {"client_id": "hivegent", "client_secret": "s", "issuer": "https://id.test"}
    config = {"url": "https://crm.test/mcp", "oauth": oauth}
    server = CatalogServer.build("crm", _config(config), recording)
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

    with pytest.raises(ValueError, match="SSE"):
        _ = CatalogServer.build("crm", _config({"url": "https://crm.test/sse"}))


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

    async with _connect(CatalogServer.build("local", config)) as server:
        assert server.integration().host == sys.executable
        assert set(server.client.tools or ()) == {"environment"}

        result = await server.client.toolset.client.call_tool("environment", {})

    assert {"PATH", "PLAIN", "TOKEN"} <= set(result.data)
    assert "HIVEGENT_SECRET" not in result.data
