"""Web-oriented agent tool registrations.

The tool factories are settings-free, and this module is where the application
settings are applied to their parameters.  Each web tool is gated on
what it needs: ``web_search`` on a search service
(``HIVEGENT_TOOLS__SEARCH_URL``), and ``web_fetch`` on a web allow list naming
at least one host (``HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS``).  Both are
unset by default, so the model answers from the indexed documents alone.
"""

from collections.abc import Iterable, Iterator
from importlib.metadata import metadata
from typing import TypedDict

from exar.tools.web import (
    HostClient,
    HostSession,
    KnownUrls,
    SearchBackendKind,
    WebFetch,
    WebSearch,
)
from pydantic import TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from ...config import settings
from ...db.conversations import prompt_items, prompt_text
from ...http_client import get_trusted_http_client, get_web_http_client
from ...shared.l10n import format_bytes
from ...tools.base import ToolFunc
from ..common import WINDOW_MAX_CHARS, AgentTools, UserDeps

__all__ = [
    "WEB_TOOLS",
    "build_user_agent",
    "record_known_urls",
    "web_enabled",
    "web_tools",
    "web_toolset",
]


def build_user_agent(contact: str | None = None) -> str:
    """Build a descriptive User-Agent identifying the app and an operator.

    Well-behaved hosts such as Wikipedia reject a generic library agent, so
    name the application and, when given, the operator's *contact* email for
    traffic questions.
    """
    meta = metadata("hivegent-backend")
    suffix = "" if contact is None else f" (+mailto:{contact})"

    return f"{meta['Name']}/{meta['Version']}{suffix}"


_user_agent = build_user_agent(settings.network.contact_email)

_FETCH_NOTES = (
    "Requests leave through an egress proxy, which also enforces the URL host policy."
)

_search_url = settings.tools.search_url
_fetch_enabled = settings.security.web_policy().has_allowlist

_backend = (
    None
    if _search_url is None
    else TypeAdapter[SearchBackendKind](SearchBackendKind).validate_python(
        {"kind": settings.tools.search_backend, "url": _search_url}
    )
)


# The pooled clients come from the lifespan, so the tools take their getters,
# which is what lets one research turn reuse a single connection.  The search
# service is an operator endpoint, so search takes the trusted client, while
# fetching takes the web client with its URL policy, egress proxy, and redirect
# limit.  The turn's known URLs and fetched pages are its deps'.
def web_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc]:
    """The enabled web tools, for one run.

    Each is gated on what it needs, which states each gate exactly once, so
    every consumer, the toolset and the sandbox, reads a list that is already
    correct.
    """
    session = HostSession("web", lambda: ctx.deps.web)
    network = settings.network
    tools: list[ToolFunc] = []

    if (search_url := _search_url) is not None and (backend := _backend) is not None:
        tools.append(
            WebSearch(
                client=HostClient(
                    "search", lambda: get_trusted_http_client(search_url)
                ),
                session=session,
                backend=backend,
                timeout_seconds=network.web_timeout_seconds,
            ).tool()
        )

    if _fetch_enabled:
        tools.append(
            WebFetch(
                client=HostClient("web", get_web_http_client),
                session=session,
                timeout_seconds=network.web_timeout_seconds,
                max_response_bytes=network.webfetch_max_response_bytes,
                max_chars=WINDOW_MAX_CHARS,
                user_agent=_user_agent,
                fast=network.webfetch_fast_extraction,
                notes=_FETCH_NOTES,
                size=format_bytes,
            ).tool()
        )

    return tools


web_toolset = AgentTools(factory=web_tools)
"""The web tools, held to the host policy."""

web_enabled = _search_url is not None or _fetch_enabled

WEB_TOOLS = frozenset({"web_search", "web_fetch"})
"""The names of the web tools, whichever of them the operator enabled."""


class _Hit(TypedDict):
    href: str


class _SearchUrls(TypedDict, total=False):
    hits: list[_Hit]


class _PageUrls(TypedDict, total=False):
    url: str
    links: list[str]


_SEARCH_URLS = TypeAdapter(list[_SearchUrls])
_PAGE_URLS = TypeAdapter(list[_PageUrls])


# Only the URL fields are read, so a past result is not validated in full,
# and a failed item of a batch, which has none, adds nothing.
def _web_urls(tool_name: str, data: object) -> Iterator[str]:
    """The URLs the structured result *data* of the web tool *tool_name* holds."""
    match tool_name:
        case "web_search":
            for result in _SEARCH_URLS.validate_python(data):
                for hit in result.get("hits", ()):
                    yield hit["href"]
        case "web_fetch":
            for page in _PAGE_URLS.validate_python(data):
                if "url" in page:
                    yield page["url"]

                yield from page.get("links", ())


def record_known_urls(known: KnownUrls, messages: Iterable[ModelMessage]) -> None:
    """Know the URLs a history holds from sources the model cannot write.

    Those are the user's prompts and the web tools' structured results, the
    same ones the tools record within a turn, so a link from an earlier turn
    can still be fetched.  The model's responses, compaction summaries among
    them, and the results of every other tool add nothing.
    Without ``web_fetch`` nothing reads them, so the history is not scanned.
    """
    if not _fetch_enabled:
        return

    parts = (
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )

    for part in parts:
        if (
            isinstance(part, ToolReturnPart)
            and part.tool_name in WEB_TOOLS
            and isinstance(part.metadata, DataChunk)
        ):
            known.add(*_web_urls(part.tool_name, part.metadata.data))
        elif isinstance(part, UserPromptPart):
            for item in prompt_items(part):
                if (text := prompt_text(item)) is not None:
                    known.add_text(text)
