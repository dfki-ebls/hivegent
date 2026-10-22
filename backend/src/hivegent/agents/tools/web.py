"""Web-oriented agent tool registrations.

The tool classes are settings-free; this module is where the application
settings are applied to their instance fields.  Each web tool is gated on
what it needs: ``web_search`` on a search service
(``HIVEGENT_TOOLS__SEARCH_URL``), and ``web_fetch`` on a web allow list naming
at least one host (``HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS``).  Both are
unset by default, so the model answers from the indexed documents alone.
"""

from collections.abc import Iterable

from pydantic_ai import FunctionToolset
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
from ...tools import (
    KnownUrls,
    OpenserpBackend,
    SearchBackend,
    SearxngBackend,
    WebFetch,
    WebSearch,
    build_user_agent,
)
from ...tools.base import ToolSpec
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps, agent_toolset

__all__ = ["WEB_FACTORIES", "record_known_urls", "web_enabled", "web_toolset"]

_user_agent = build_user_agent(settings.network.contact_email)

_search_url = settings.tools.search_url or ""
_fetch_enabled = settings.security.web_policy().has_allowlist

_BACKENDS: dict[str, type[SearchBackend]] = {
    "searxng": SearxngBackend,
    "openserp": OpenserpBackend,
}
_backend = _BACKENDS[settings.tools.search_backend](_search_url)


# A factory runs per tool call, so it only wires up fields, and the pooled clients
# come from the lifespan, which is what lets one research turn reuse a single
# connection.  The search service is an operator endpoint, so search takes
# the trusted client, while fetching takes the web client with its URL policy,
# egress proxy, and redirect limit.
def _web_search(deps: UserDeps) -> WebSearch:
    return WebSearch(
        client=get_trusted_http_client(),
        backend=_backend,
        known_urls=deps.known_urls,
        timeout_seconds=settings.network.web_timeout_seconds,
    )


def _web_fetch(deps: UserDeps) -> WebFetch:
    network = settings.network

    return WebFetch(
        client=get_web_http_client(),
        known_urls=deps.known_urls,
        timeout_seconds=network.web_timeout_seconds,
        max_response_bytes=network.webfetch_max_response_bytes,
        fast_extraction=network.webfetch_fast_extraction,
        user_agent=_user_agent,
    )


web_toolset: FunctionToolset[UserDeps] = agent_toolset()

WEB_FACTORIES = tuple(
    factory
    for factory, enabled in (
        (_web_search, bool(_search_url)),
        (_web_fetch, _fetch_enabled),
    )
    if enabled
)
"""The enabled web tools, which is what states each gate exactly once.

Every consumer, the toolset below and the sandbox surface, then reads a list
that is already correct, rather than re-deriving the gates for itself.
"""

web_enabled = bool(WEB_FACTORIES)

if WEB_FACTORIES:
    register_agent_tools(web_toolset, UserDeps, WEB_FACTORIES)


_WEB_RESULTS = {
    spec.name: spec.data_adapter
    for spec in map(ToolSpec.from_factory, (_web_search, _web_fetch))
}
"""The adapter each web tool's structured result was serialized with."""


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
            and (adapter := _WEB_RESULTS.get(part.tool_name))
            and isinstance(part.metadata, DataChunk)
        ):
            known.add_results(adapter.validate_python(part.metadata.data))
        elif isinstance(part, UserPromptPart):
            for item in prompt_items(part):
                if (text := prompt_text(item)) is not None:
                    known.add_text(text)
