"""Unit tests for the web search/fetch tools and the URL policy."""

from dataclasses import asdict
from typing import Any

import httpx2
import pytest
from exar.tools import ItemFailure, Retry
from exar.tools.web import (
    HostClient,
    HostSession,
    KnownUrls,
    SearchBackend,
    Searxng,
    WebFetch,
    WebQuery,
    WebRead,
    WebSearch,
    WebSearchHit,
    WebSearchResults,
    WebSession,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from hivegent.agents.output import DATA_CHUNK_TYPE
from hivegent.agents.tools.web import build_user_agent, record_known_urls
from hivegent.config.security import SecuritySettings, UrlPolicySettings
from hivegent.security import UnsafeUrlError, UrlPolicy, create_safe_async_client
from hivegent.shared.l10n import format_bytes
from hivegent.tools.base import ToolFunc
from tests.helpers import single

#: Permits every host, so a fetch test opts out of policy enforcement.
_ANY_HOST = UrlPolicy(allow_hosts=("*",))


def test_user_agent_uses_backend_distribution_metadata() -> None:
    user_agent = build_user_agent("test@example.com")

    assert user_agent.startswith("hivegent-backend/")
    assert user_agent.endswith(" (+mailto:test@example.com)")


def _web_client(
    handler: Any,
    policy: UrlPolicy,
    max_redirects: int = 5,
) -> httpx2.AsyncClient:
    """Build the pooled web client the tools take, over a mock transport.

    Only the network layer is substituted, so the real client is built and its
    request hook applies the host policy on every hop exactly as in production.
    """
    return create_safe_async_client(
        policy=policy,
        proxy_url="http://127.0.0.1:4750",
        timeout=10.0,
        follow_redirects=True,
        max_redirects=max_redirects,
        transport=httpx2.MockTransport(handler),
    )


class TestUrlPolicy:
    """Host allow/deny semantics."""

    def test_the_web_policy_defaults_to_deny_all(self) -> None:
        with pytest.raises(UnsafeUrlError, match="allowlist"):
            SecuritySettings().web_policy().check_host("api.example.com")

    @pytest.mark.parametrize(
        ("host", "refusal"),
        [
            ("example.com", None),
            ("docs.example.com", None),
            ("internal.example.com", "blocked"),
            ("evil.com", "allowlist"),
            ("notexample.com", "allowlist"),
        ],
    )
    def test_an_entry_matches_its_domain_and_subdomains_and_deny_wins(
        self, host: str, refusal: str | None
    ) -> None:
        policy = SecuritySettings(
            web_urls=UrlPolicySettings(
                allow_hosts=["example.com"], deny_hosts=["internal.example.com"]
            ),
        ).web_policy()

        if refusal is None:
            policy.check_host(host)
        else:
            with pytest.raises(UnsafeUrlError, match=refusal):
                policy.check_host(host)

    async def test_a_safe_client_rejects_credentials_in_a_url(self) -> None:
        async with create_safe_async_client(
            policy=_ANY_HOST,
            proxy_url="http://127.0.0.1:4750",
            timeout=1.0,
            transport=httpx2.MockTransport(lambda _request: httpx2.Response(200)),
        ) as client:
            await client.get("https://example.com/page")

            with pytest.raises(UnsafeUrlError, match="credentials"):
                await client.get("https://user:pw@example.com")


def _search_tool(
    handler: Any, backend: SearchBackend | None = None
) -> tuple[ToolFunc, KnownUrls]:
    """Build web_search with a trusted client backed by a mock transport, and its known URLs."""
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    session = WebSession()
    tool = WebSearch(
        client=HostClient("search", lambda: client),
        session=HostSession("web", lambda: session),
        backend=backend or Searxng(url=_SEARCH_URL),
        timeout_seconds=10.0,
    ).tool()

    return tool, session.known


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

        tool, known = _search_tool(handler)
        out = await tool(
            [
                WebQuery("jura e7", "news", "de", "week"),
                WebQuery("jura"),
                WebQuery("jura", "news"),
            ]
        )
        first, second, third = out.metadata

        assert not isinstance(first, ItemFailure)
        assert not isinstance(second, ItemFailure)
        # Only fetchable links survive, and whitespace is collapsed.
        assert first.hits == (
            WebSearchHit("Manual", "https://a.example/manual", "a b", "2026-10-17"),
        )
        # A page an earlier search found is not listed again.
        assert [hit.href for hit in second.hits] == ["https://b.example/"]
        assert "1 page already listed above" in out.return_value
        # The same query with other filters is a search of its own.
        assert not isinstance(third, ItemFailure)
        assert "==> jura [news] <==" in out.return_value
        assert "published 2026-10-17" in out.return_value
        # Every listed result may be fetched next.
        assert "https://a.example/manual" in known
        assert "https://b.example/" in known

    @pytest.mark.parametrize(
        ("status", "body", "message"),
        [
            (400, {}, "web search failed"),
            (
                200,
                {"results": [], "unresponsive_engines": [["brave", "CAPTCHA"]]},
                "brave: CAPTCHA",
            ),
        ],
        ids=["api-failure", "unresponsive-engine"],
    )
    async def test_a_search_that_failed_is_a_retry_naming_why(
        self, status: int, body: dict[str, Any], message: str
    ) -> None:
        tool, _ = _search_tool(lambda _request: httpx2.Response(status, json=body))

        with pytest.raises(Retry, match=message):
            await tool([WebQuery("anything")])


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


_POLICY = UrlPolicy(allow_hosts=("example.com",), deny_hosts=("evil.com",))
_PATHS = ("start", "out", "loop")


def _site(request: httpx2.Request) -> httpx2.Response:
    """A site whose `start` redirects to its article, `out` to a denied host, and `loop` to itself."""
    redirects = {
        "/start": "/final",
        "/out": "https://evil.com/x",
        "/loop": "/loop",
    }

    if request.url.path in redirects:
        return httpx2.Response(302, headers={"location": redirects[request.url.path]})

    assert request.url.path == "/final", "an unknown URL must not be requested"

    return httpx2.Response(
        200, content=HTML, headers={"content-type": "text/html; charset=utf-8"}
    )


def _fetch_tool() -> tuple[ToolFunc, KnownUrls]:
    """Build web_fetch over :func:`_site` behind the host policy, and its known URLs.

    The pages the tests ask for are known, as if the user had named them.
    """
    client = _web_client(_site, _POLICY, max_redirects=3)
    session = WebSession()
    session.known.add(*(f"https://example.com/{path}" for path in _PATHS))

    tool = WebFetch(
        client=HostClient("web", lambda: client),
        session=HostSession("web", lambda: session),
        timeout_seconds=10.0,
        size=format_bytes,
    ).tool()

    return tool, session.known


class TestWebFetch:
    """Redirect validation, HTML extraction, and the pages a fetch may name."""

    async def test_html_is_converted_and_redirects_followed(self) -> None:
        tool, known = _fetch_tool()

        out = await tool([WebRead("https://example.com/start")])

        page = single(out.metadata)
        # A link on the fetched page may be fetched next.
        assert "https://example.com/docs/guide" in known
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
        assert f"\n{header}\n1: " in out.return_value

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("https://example.com/out", "blocked"),
            ("https://example.com/loop", "redirects"),
            # Only URLs from the user or the web tools may be fetched.
            ("https://example.com/unknown", "not appeared in the conversation"),
        ],
        ids=["redirect-to-denied-host", "redirect-loop", "unknown-url"],
    )
    async def test_a_fetch_that_may_not_happen_is_a_retry(
        self, url: str, message: str
    ) -> None:
        tool, _ = _fetch_tool()

        with pytest.raises(Retry, match=message):
            await tool([WebRead(url)])


class TestKnownUrls:
    def test_history_counts_only_user_and_web_tool_text(self) -> None:
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
            fetch_enabled=True,
        )

        assert "https://user.example/a" in known
        assert "https://hit.example/" in known
        assert "https://model.example/leak" not in known
        assert "https://program.example/" not in known
