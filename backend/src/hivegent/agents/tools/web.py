"""Web-oriented agent tool registrations.

The tool classes are settings-free; this module is where the application
settings are applied to their instance fields.  The web tools are only
registered while ``HIVEGENT_TOOLS__ENABLE_WEB`` is set (the
operator master switch, off by default) and a web host policy is
configured (``HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS`` / ``__DENY_HOSTS``,
which default to the Wikimedia projects): without the switch the model
answers from the indexed documents alone, and without a policy letting
it dereference arbitrary websites is unsafe and a search whose results
could never be opened is pointless — so in either case both tools are
hidden altogether.
"""

from pydantic_ai import FunctionToolset

from ...config import settings
from ...tools import WebFetch, WebSearch, build_user_agent
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["web_toolset"]

_policy = settings.security.web_policy()
_user_agent = build_user_agent(settings.network.contact_email)


def _web_search(_deps: UserDeps) -> WebSearch:
    return WebSearch(
        language=settings.network.websearch_language,
        user_agent=_user_agent,
        policy=_policy,
    )


def _web_fetch(_deps: UserDeps) -> WebFetch:
    network = settings.network
    return WebFetch(
        timeout_seconds=network.webfetch_timeout_seconds,
        max_response_bytes=network.webfetch_max_response_bytes,
        max_chars=network.webfetch_max_chars,
        max_redirects=network.webfetch_max_redirects,
        user_agent=_user_agent,
        policy=_policy,
    )


web_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)

if settings.tools.enable_web and _policy.restricts_hosts:
    register_agent_tools(
        web_toolset,
        UserDeps,
        [
            _web_search,
            _web_fetch,
        ],
    )
