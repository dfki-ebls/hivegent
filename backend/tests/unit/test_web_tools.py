"""Unit tests for the web search/fetch tools and the URL policy."""

import re
from dataclasses import asdict
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from hivegent import security
from hivegent.agents.tools import web as agent_web
from hivegent.agents.tools.web import record_known_urls
from hivegent.config import SecuritySettings, UrlPolicySettings
from hivegent.security import (
    UrlPolicy,
    create_safe_async_client,
    require_safe_external_url,
)
from hivegent.tools.base import ItemFailure, ToolRetry
from hivegent.tools.pydantic_ai import DATA_CHUNK_TYPE
from hivegent.tools.web import (
    KnownUrls,
    OpenserpBackend,
    SearchBackend,
    SearxngBackend,
    WebFetch,
    WebQuery,
    WebSearch,
    WebSearchHit,
    WebSearchResults,
    build_user_agent,
)
from tests.helpers import single

#: Permits every host, so a fetch test opts out of policy enforcement.
_ANY_HOST = UrlPolicy(allow_hosts=("*",))


def test_user_agent_uses_backend_distribution_metadata() -> None:
    user_agent = build_user_agent("test@example.com")

    assert user_agent.startswith("hivegent-backend/")
    assert user_agent.endswith(" (+mailto:test@example.com)")


def _web_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    policy: UrlPolicy,
    max_redirects: int = 5,
) -> httpx2.AsyncClient:
    """Build the pooled web client the tools take, over a mock transport.

    Only the network layer is substituted, so the real client is built and its
    request hook applies the host policy on every hop exactly as in production.
    """
    monkeypatch.setattr(
        security, "_egress_transport", lambda *_: httpx2.MockTransport(handler)
    )

    return create_safe_async_client(
        policy=policy,
        proxy_url="http://127.0.0.1:4750",
        timeout=10.0,
        follow_redirects=True,
        max_redirects=max_redirects,
    )


class TestUrlPolicy:
    """Host allow/deny semantics."""

    def test_user_policy_defaults_to_deny_all(self) -> None:
        with pytest.raises(ValueError, match="allowlist"):
            SecuritySettings().user_policy().check_host("api.example.com")

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
            user_urls=UrlPolicySettings(
                allow_hosts=["llm.corp.example"], deny_hosts=["evil.com"]
            ),
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

    def test_external_url_validation_rejects_unsafe_urls(self) -> None:
        policy = UrlPolicy(allow_hosts=("*",))
        require_safe_external_url("https://example.com/page", "url", policy=policy)
        for url in (
            "ftp://example.com",
            "https://user:pw@example.com",
            "",
        ):
            with pytest.raises(ValueError):
                require_safe_external_url(url, "url", policy=policy)

        with pytest.raises(ValueError, match="allowlist"):
            require_safe_external_url("https://example.com", "url", policy=UrlPolicy())


def _search_tool(handler: Any, backend: SearchBackend | None = None) -> WebSearch:
    """Build a WebSearch whose trusted client is backed by a mock transport."""
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    return WebSearch(
        client=client,
        backend=backend or SearxngBackend(_SEARCH_URL),
        known_urls=KnownUrls(),
        timeout_seconds=10.0,
    )


_SEARCH_URL = "http://127.0.0.1:8888"


def _result(url: str, title: str = "", content: str = "") -> dict[str, Any]:
    """One result of a SearXNG JSON response."""
    return {"url": url, "title": title, "content": content}


class TestWebSearch:
    """SearXNG API call, result shaping, and failure surfacing."""

    async def test_results_are_shaped_and_deduplicated(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            params = request.url.params
            assert request.url.path == "/search"
            assert params["format"] == "json"

            if params["q"] == "jura e7":
                assert params["categories"] == "news"
                assert params["language"] == "de"
                assert params["time_range"] == "week"
                results = [
                    {
                        **_result("https://a.example/manual", "Manual", " a\n  b "),
                        "publishedDate": "2026-10-17T08:00:00",
                    },
                    _result("javascript:alert(1)", "Unsafe"),
                ]
            else:
                # Unset filters are left to the instance's defaults.
                assert "language" not in params
                assert "time_range" not in params
                results = [
                    _result("https://a.example/manual", "Manual"),
                    _result("https://b.example/", "Other"),
                ]

            return httpx2.Response(200, json={"results": results})

        tool = _search_tool(handler)
        out = await tool(
            [
                WebQuery("jura e7", "news", "de", "week"),
                WebQuery("jura"),
                WebQuery("jura", "news"),
            ]
        )
        first, second, third = out.data

        assert not isinstance(first, ItemFailure)
        assert not isinstance(second, ItemFailure)
        # Only fetchable links survive, and whitespace is collapsed.
        assert first.hits == (
            WebSearchHit("Manual", "https://a.example/manual", "a b", "2026-10-17"),
        )
        # A page an earlier search found is not listed again.
        assert [hit.href for hit in second.hits] == ["https://b.example/"]
        assert "1 page already listed above" in out.text
        # The same query with other filters is a search of its own.
        assert not isinstance(third, ItemFailure)
        assert "==> jura [news] <==" in out.text
        assert "published 2026-10-17" in out.text
        # Every listed result may be fetched next.
        assert "https://a.example/manual" in tool.known_urls
        assert "https://b.example/" in tool.known_urls

    async def test_openserp_megasearch_is_translated(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            params = request.url.params
            assert request.url.path == "/mega/search"
            assert (params["text"], params["limit"]) == ("jura", "3")
            assert (params["lang"], params["region"]) == ("DE", "AT")
            assert re.fullmatch(r"\d{8}\.\.\d{8}", params["date"])

            return httpx2.Response(
                200,
                json={
                    "results": [
                        {"url": "https://a.example/", "title": "A", "snippet": "s"}
                    ],
                    "meta": {"engines_failed": ["google"]},
                },
            )

        out = await _search_tool(handler, OpenserpBackend(_SEARCH_URL))(
            [WebQuery("jura", language="de-AT", time_range="month")], max_results=3
        )

        assert single(out.data).hits == (WebSearchHit("A", "https://a.example/", "s"),)

    async def test_api_failure_raises_tool_retry(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(400)

        with pytest.raises(ToolRetry, match="web search failed"):
            await _search_tool(handler)([WebQuery("anything", language="xx")])

    async def test_unresponsive_engines_are_reported(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={"results": [], "unresponsive_engines": [["brave", "CAPTCHA"]]},
            )

        with pytest.raises(ToolRetry, match="brave: CAPTCHA"):
            await _search_tool(handler)([WebQuery("anything")])


_ARTICLE = b"An article needs a few hundred characters to count as main content. " * 5
HTML = (
    b"""
<html><head><title>Test Page | Site</title><script>var x = 1;</script>
<meta property="article:published_time" content="2026-10-17T08:00:00Z"></head>
<body><nav><a href="/home">Home</a> <a href="/about">About</a></nav>
<article><h1>Hello</h1>
<p>World, see the <a href="/docs/guide">guide</a> for the details.</p>
<p>"""
    + _ARTICLE
    + b"""</p></article>
<footer>Cookie banner</footer><script>tracking();</script></body></html>
"""
)


def _fetch_tool(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    policy: UrlPolicy = _ANY_HOST,
    max_redirects: int = 5,
    **kwargs: Any,
) -> WebFetch:
    """Build a WebFetch whose pooled client is backed by a mock transport.

    ``max_redirects`` binds the client, not the tool, because HTTPX accepts
    redirect settings only there.  The pages the tests ask for are known, as if
    the user had named them.
    """
    client = _web_client(monkeypatch, handler, policy, max_redirects)
    known = KnownUrls()
    known.add(*(f"https://example.com/{path}" for path in _PATHS))

    return WebFetch(client=client, known_urls=known, timeout_seconds=10.0, **kwargs)


_PATHS = ("start", "final", "gone", "img", "big", "page", "loop", "index")


class TestWebFetch:
    """Redirect validation, HTML extraction, caps, and content-type gate."""

    async def test_html_is_converted_and_redirects_followed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/start":
                return httpx2.Response(302, headers={"location": "/final"})
            return httpx2.Response(
                200, content=HTML, headers={"content-type": "text/html; charset=utf-8"}
            )

        tool = _fetch_tool(monkeypatch, handler)
        out = await tool(["https://example.com/start"])

        page = single(out.data)
        # A link on the fetched page may be fetched next.
        assert "https://example.com/docs/guide" in tool.known_urls
        assert page.url == "https://example.com/final"
        assert (page.title, page.published) == ("Hello", "2026-10-17")
        assert "# Hello" in page.content
        # Links resolve against the final URL, so the model can follow them.
        assert "[guide](https://example.com/docs/guide)" in page.content
        # Scripts and the page around the article are dropped.
        for boilerplate in ("tracking", "About", "Cookie banner"):
            assert boilerplate not in page.content
        # Formatted output numbers every content line for citations.
        header = "Hello (https://example.com/final), published 2026-10-17"
        assert f"\n{header}\n1: " in out.text

    async def test_pages_fail_alone_and_a_page_is_shown_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/start":
                return httpx2.Response(302, headers={"location": "/final"})

            if request.url.path == "/gone":
                return httpx2.Response(403)

            return httpx2.Response(
                200, content=b"hi", headers={"content-type": "text/plain"}
            )

        tool = _fetch_tool(monkeypatch, handler)
        urls = [f"https://example.com/{path}" for path in ("start", "final", "gone")]
        out = await tool(urls)

        page, duplicate, gone = out.data
        assert not isinstance(page, ItemFailure)
        assert (page.requested_url, page.url) == (urls[0], urls[1])
        assert duplicate == ItemFailure(urls[1], f"same result as {urls[0]}")
        assert gone == ItemFailure(urls[2], "HTTP 403.")

    async def test_redirect_to_denied_host_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.host == "example.com":
                return httpx2.Response(302, headers={"location": "https://evil.com/x"})
            return httpx2.Response(
                200, content=b"hi", headers={"content-type": "text/plain"}
            )

        tool = _fetch_tool(
            monkeypatch,
            handler,
            UrlPolicy(allow_hosts=("example.com",), deny_hosts=("evil.com",)),
        )
        with pytest.raises(ToolRetry, match="blocked"):
            await tool(["https://example.com/start"])

    async def test_unsupported_content_type_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, content=b"\x89PNG", headers={"content-type": "image/png"}
            )

        tool = _fetch_tool(monkeypatch, handler)
        with pytest.raises(ToolRetry, match="unsupported content type"):
            await tool(["https://example.com/img"])

    async def test_content_is_truncated_to_caps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, content=b"a" * 100, headers={"content-type": "text/plain"}
            )

        tool = _fetch_tool(monkeypatch, handler, max_chars=10)
        out = await tool(["https://example.com/big"])
        assert single(out.data).content == "a" * 10
        assert single(out.data).truncated
        assert out.text.endswith("[truncated]")

    async def test_long_line_truncated_in_formatted_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A data-URI line must not flood the context: the numbered output
        # truncates it while the structured content keeps it intact.
        long_line = "data:image/png;base64," + "A" * 5_000
        body = f"intro\n{long_line}\ntail".encode()

        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, content=body, headers={"content-type": "text/plain"}
            )

        tool = _fetch_tool(monkeypatch, handler, max_line_chars=80)
        out = await tool(["https://example.com/page"])
        assert "…" in out.text
        assert len(max(out.text.splitlines(), key=len)) < 200
        assert long_line in single(out.data).content

    async def test_too_many_redirects_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(302, headers={"location": "/loop"})

        tool = _fetch_tool(monkeypatch, handler, max_redirects=3)
        with pytest.raises(ToolRetry, match="redirects"):
            await tool(["https://example.com/loop"])


class TestKnownUrls:
    """Only URLs from the user or the web tools may be fetched."""

    async def test_links_of_a_link_list_can_be_followed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        items = "".join(f'<li><a href="/p_{i}">Page {i}</a></li>' for i in range(5))
        html = f"<html><body><h1>Docs</h1><ul>{items}</ul></body></html>"

        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, content=html.encode(), headers={"content-type": "text/html"}
            )

        tool = _fetch_tool(monkeypatch, handler)
        out = await tool(["https://example.com/index"])

        assert "[Page 3](https://example.com/p_3)" in single(out.data).content
        assert "https://example.com/p_3" in tool.known_urls

    async def test_unknown_url_is_refused_without_a_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("an unknown URL must not be requested")

        tool = _fetch_tool(monkeypatch, handler)

        with pytest.raises(ToolRetry, match="not appeared in the conversation"):
            await tool(["https://example.com/start?secret=1"])

    def test_history_counts_only_user_and_web_tool_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent_web, "_fetch_enabled", True)
        known = KnownUrls()
        hit = WebSearchHit("A", "https://hit.example/", "")
        record_known_urls(
            known,
            [
                ModelRequest(parts=[UserPromptPart("read https://user.example/a.")]),
                ModelResponse(parts=[TextPart("https://model.example/leak")]),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "web_search",
                            "[1] A (https://hit.example/)",
                            metadata=DataChunk(
                                type=DATA_CHUNK_TYPE,
                                data=[asdict(WebSearchResults("a", (hit,)))],
                            ),
                        ),
                        ToolReturnPart("run_python", "https://program.example/"),
                    ]
                ),
            ],
        )

        assert "https://user.example/a" in known
        assert "https://hit.example/" in known
        assert "https://model.example/leak" not in known
        assert "https://program.example/" not in known
