"""Unit tests for the web search/fetch tools and the URL policy."""

from typing import Any

import httpx
import pytest
from ddgs.exceptions import DDGSException

import hivegent.tools.web as web_module
from hivegent.config import SecuritySettings, UrlPolicySettings
from hivegent.security import SafeAsyncHTTPTransport, UrlPolicy, is_safe_external_url
from hivegent.tools.base import ToolRetry
from hivegent.tools.web import WebFetch, WebSearch


class TestUrlPolicy:
    """Host allow/deny semantics."""

    def test_entry_matches_domain_and_subdomains(self) -> None:
        policy = UrlPolicy(allow_hosts=("example.com",))
        policy.check_host("example.com")
        policy.check_host("docs.example.com")
        with pytest.raises(ValueError, match="allowlist"):
            policy.check_host("evil.com")
        with pytest.raises(ValueError, match="allowlist"):
            policy.check_host("notexample.com")

    def test_deny_list_wins_over_allow_list(self) -> None:
        policy = UrlPolicy(
            allow_hosts=("example.com",), deny_hosts=("internal.example.com",)
        )
        policy.check_host("docs.example.com")
        with pytest.raises(ValueError, match="blocked"):
            policy.check_host("internal.example.com")

    def test_settings_policies_are_independent(self) -> None:
        sec = SecuritySettings(
            web_urls=UrlPolicySettings(
                allow_hosts=["wikipedia.org"], deny_hosts=["test.wikipedia.org"]
            ),
            user_urls=UrlPolicySettings(deny_hosts=["evil.com"]),
        )
        web = sec.web_policy()
        web.check_host("de.wikipedia.org")
        with pytest.raises(ValueError, match="blocked"):
            web.check_host("test.wikipedia.org")
        # The user URL policy governs LLM/MCP URLs, not browsing.
        user = sec.user_policy()
        user.check_host("llm.corp.example")
        with pytest.raises(ValueError, match="blocked"):
            user.check_host("evil.com")

    def test_is_safe_external_url_rejects_unsafe_shapes(self) -> None:
        policy = UrlPolicy()
        assert is_safe_external_url("https://example.com/page", policy=policy)
        assert not is_safe_external_url("ftp://example.com", policy=policy)
        assert not is_safe_external_url("https://user:pw@example.com", policy=policy)
        assert not is_safe_external_url("http://127.0.0.1/x", policy=policy)
        assert not is_safe_external_url("", policy=policy)


class _FakeDDGS:
    """Stand-in for ddgs.DDGS with canned results or a canned error."""

    results: list[dict[str, str]] = []
    error: Exception | None = None

    def __enter__(self) -> "_FakeDDGS":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        if self.error is not None:
            raise self.error
        return self.results


class TestWebSearch:
    """Failure surfacing and result filtering."""

    def test_backend_failure_raises_tool_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_FakeDDGS, "error", DDGSException("No results found."))
        monkeypatch.setattr(web_module, "DDGS", _FakeDDGS)
        with pytest.raises(ToolRetry, match="returned nothing"):
            WebSearch()("anything")

    def test_unsafe_results_are_filtered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_FakeDDGS, "error", None)
        monkeypatch.setattr(
            _FakeDDGS,
            "results",
            [
                {"title": "ok", "href": "https://example.com/a", "body": "snippet"},
                {"title": "bad", "href": "http://127.0.0.1/admin", "body": "x"},
                {"title": "denied", "href": "https://evil.com/b", "body": "y"},
            ],
        )
        monkeypatch.setattr(web_module, "DDGS", _FakeDDGS)
        out = WebSearch(policy=UrlPolicy(deny_hosts=("evil.com",)))("query")
        assert [r["href"] for r in out.data] == ["https://example.com/a"]
        assert "[1] ok (https://example.com/a)" in out.text
        assert "snippet" in out.text


HTML = b"""
<html><head><title>Test Page</title><script>var x = 1;</script></head>
<body><h1>Hello</h1><p>World</p><script>tracking();</script></body></html>
"""


def _fetch_tool(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    **kwargs: Any,
) -> WebFetch:
    """Build a WebFetch whose safe client is backed by a mock transport.

    The real :class:`SafeAsyncHTTPTransport` wraps the mock, so the URL
    shape and host-policy checks run on every hop exactly as in
    production; only the network layer is substituted.
    """

    def fake_client(*, policy: UrlPolicy, **client_kwargs: Any) -> httpx.AsyncClient:
        transport = SafeAsyncHTTPTransport(
            policy=policy, inner=httpx.MockTransport(handler)
        )
        return httpx.AsyncClient(transport=transport, **client_kwargs)

    monkeypatch.setattr(web_module, "create_safe_async_client", fake_client)
    return WebFetch(**kwargs)


class TestWebFetch:
    """Redirect validation, HTML extraction, caps, and content-type gate."""

    async def test_html_is_converted_and_redirects_followed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/final"})
            return httpx.Response(
                200, content=HTML, headers={"content-type": "text/html; charset=utf-8"}
            )

        tool = _fetch_tool(monkeypatch, handler)
        out = await tool("https://example.com/start")

        assert out.data.url == "https://example.com/final"
        assert out.data.title == "Test Page"
        assert "# Hello" in out.data.content
        assert "tracking" not in out.data.content
        # Formatted output numbers every content line for citations.
        assert out.text.startswith("Test Page — https://example.com/final\n1: ")

    async def test_redirect_to_denied_host_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "https://evil.com/x"})
            return httpx.Response(
                200, content=b"hi", headers={"content-type": "text/plain"}
            )

        tool = _fetch_tool(
            monkeypatch, handler, policy=UrlPolicy(deny_hosts=("evil.com",))
        )
        with pytest.raises(ToolRetry, match="blocked"):
            await tool("https://example.com/start")

    async def test_unsupported_content_type_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"\x89PNG", headers={"content-type": "image/png"}
            )

        tool = _fetch_tool(monkeypatch, handler)
        with pytest.raises(ToolRetry, match="unsupported content type"):
            await tool("https://example.com/img")

    async def test_content_is_truncated_to_caps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"a" * 100, headers={"content-type": "text/plain"}
            )

        tool = _fetch_tool(monkeypatch, handler, max_chars=10)
        out = await tool("https://example.com/big")
        assert out.data.content == "a" * 10
        assert out.data.truncated
        assert out.text.endswith("[truncated]")

    async def test_too_many_redirects_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "/loop"})

        tool = _fetch_tool(monkeypatch, handler, max_redirects=3)
        with pytest.raises(ToolRetry, match="too many redirects"):
            await tool("https://example.com/loop")
