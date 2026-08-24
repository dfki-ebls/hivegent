"""Web-oriented agent tool registrations.

The tool classes are settings-free; this module is where the application
settings are applied to their instance fields.  The web tools are only
registered while ``HIVEGENT_TOOLS__ENABLE_WEB`` is set (the
operator master switch, off by default) and the web allow list names at
least one host (``HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS``, which
defaults to the Wikimedia projects): without the switch the model
answers from the indexed documents alone, and with an empty allow list
every fetch would be refused, so a search whose results could never be
opened is pointless — in either case both tools are hidden altogether.
"""

from pydantic_ai import FunctionToolset

from ...config import settings
from ...http_client import get_web_http_client
from ...tools import WebFetch, WebSearch, build_user_agent
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["web_enabled", "web_toolset"]

_user_agent = build_user_agent(settings.network.contact_email)

# The web tools are live only with the operator switch on *and* a host policy to
# constrain them; the single flag every consumer gates on so the two conditions
# never drift apart.
web_enabled = settings.tools.enable_web and settings.security.web_policy().has_allowlist


# A factory runs per tool call, so it only wires up fields — the pooled web
# client (URL policy, egress proxy, and redirect limit) comes from the
# lifespan, which is what lets one research turn reuse a single connection.
def _web_search(_deps: UserDeps) -> WebSearch:
    return WebSearch(
        client=get_web_http_client(),
        language=settings.network.websearch_language,
        user_agent=_user_agent,
    )


def _web_fetch(_deps: UserDeps) -> WebFetch:
    network = settings.network
    return WebFetch(
        client=get_web_http_client(),
        timeout_seconds=network.webfetch_timeout_seconds,
        max_response_bytes=network.webfetch_max_response_bytes,
        max_chars=network.webfetch_max_chars,
        max_line_chars=network.webfetch_max_line_chars,
        max_formatted_chars=network.webfetch_max_formatted_chars,
        user_agent=_user_agent,
    )


web_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)

if web_enabled:
    register_agent_tools(
        web_toolset,
        UserDeps,
        [
            _web_search,
            _web_fetch,
        ],
    )
