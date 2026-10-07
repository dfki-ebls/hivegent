"""Unit tests for the web search/fetch tools and the URL policy."""

from typing import Any

import httpx2
import pytest

from hivegent import security
from hivegent.config import SecuritySettings, UrlPolicySettings
from hivegent.security import (
    UrlPolicy,
    create_safe_async_client,
    require_safe_external_url,
)
from hivegent.tools.base import ItemFailure, ToolRetry
from hivegent.tools.web import WebFetch, WebSearch, WikipediaSearch, build_user_agent
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


def _search_response(*titles_and_snippets: tuple[str, str]) -> dict[str, Any]:
    """A MediaWiki ``list=search`` JSON body for the given hits."""
    return {
        "query": {
            "search": [
                {"title": title, "snippet": snippet}
                for title, snippet in titles_and_snippets
            ]
        }
    }


class TestWebSearch:
    """Wikipedia API call, result shaping, and failure surfacing."""

    async def test_results_are_shaped_from_the_configured_edition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            # The configured default picks the edition when the call names none.
            assert request.url.host == "de.wikipedia.org"
            assert request.url.params["srsearch"] == "ChatGPT"
            # The configured operator User-Agent is sent on the request.
            assert request.headers["user-agent"] == "hivegent-test (+mailto:a@b.org)"
            return httpx2.Response(
                200,
                json=_search_response(
                    ("ChatGPT", 'a <span class="searchmatch">ChatGPT</span> bot'),
                    ("GPT-4", "a language model"),
                ),
            )

        out = await WebSearch(
            client=_web_client(
                monkeypatch, handler, UrlPolicy(allow_hosts=("wikipedia.org",))
            ),
            default_edition="de",
            user_agent="hivegent-test (+mailto:a@b.org)",
        )([WikipediaSearch("ChatGPT")])
        hits = single(out.data).hits

        assert [hit.href for hit in hits] == [
            "https://de.wikipedia.org/wiki/ChatGPT",
            "https://de.wikipedia.org/wiki/GPT-4",
        ]
        # The highlighted snippet HTML is reduced to plain text.
        assert hits[0].body == "a ChatGPT bot"
        assert "[1] ChatGPT (https://de.wikipedia.org/wiki/ChatGPT)" in out.text

    async def test_call_picks_the_edition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.url.host == "fr.wikipedia.org"
            return httpx2.Response(200, json=_search_response(("Paris", "")))

        client = _web_client(
            monkeypatch, handler, UrlPolicy(allow_hosts=("wikipedia.org",))
        )
        out = await WebSearch(client=client)([WikipediaSearch("Paris", edition="fr")])

        assert single(out.data).hits[0].href == "https://fr.wikipedia.org/wiki/Paris"

    async def test_an_article_two_searches_find_is_listed_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.host == "xx.wikipedia.org":
                return httpx2.Response(404)

            hits = {"Paris": [("Paris", "")], "France": [("France", ""), ("Paris", "")]}

            return httpx2.Response(
                200, json=_search_response(*hits[request.url.params["srsearch"]])
            )

        client = _web_client(
            monkeypatch, handler, UrlPolicy(allow_hosts=("wikipedia.org",))
        )
        out = await WebSearch(client=client)(
            [
                WikipediaSearch("Paris"),
                WikipediaSearch("France"),
                WikipediaSearch("Paris", edition="xx"),
            ]
        )

        paris, france, failed = out.data
        assert not isinstance(paris, ItemFailure)
        assert not isinstance(france, ItemFailure)
        assert [hit.title for hit in paris.hits] == ["Paris"]
        assert [hit.title for hit in france.hits] == ["France"]
        assert "1 article already listed above" in out.text
        assert isinstance(failed, ItemFailure)

    async def test_api_failure_raises_tool_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(503)

        client = _web_client(
            monkeypatch, handler, UrlPolicy(allow_hosts=("wikipedia.org",))
        )
        with pytest.raises(ToolRetry, match="web search failed"):
            await WebSearch(client=client)([WikipediaSearch("anything")])


HTML = b"""
<html><head><title>Test Page</title><script>var x = 1;</script></head>
<body><h1>Hello</h1><p>World</p><script>tracking();</script></body></html>
"""


def _fetch_tool(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    policy: UrlPolicy = _ANY_HOST,
    max_redirects: int = 5,
    **kwargs: Any,
) -> WebFetch:
    """Build a WebFetch whose pooled client is backed by a mock transport.

    ``max_redirects`` binds the client, not the tool, because HTTPX accepts
    redirect settings only there.
    """
    client = _web_client(monkeypatch, handler, policy, max_redirects)
    return WebFetch(client=client, **kwargs)


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

        assert single(out.data).url == "https://example.com/final"
        assert single(out.data).title == "Test Page"
        assert "# Hello" in single(out.data).content
        assert "tracking" not in single(out.data).content
        # Formatted output numbers every content line for citations.
        assert "\nTest Page — https://example.com/final\n1: " in out.text

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
        with pytest.raises(ToolRetry, match="too many redirects"):
            await tool(["https://example.com/loop"])
